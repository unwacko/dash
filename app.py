"""
QueueLess - a minimal queue management system (Python + Streamlit)

Two interfaces:
  - Customer: join with a phone number, see live position and wait estimate, cancel anytime
  - Business: ordered dashboard, add walk-ins, call next, prioritise, notify, remove

Run locally:  streamlit run app.py
"""
import re
import threading
from dataclasses import dataclass
from datetime import datetime

import streamlit as st

st.set_page_config(page_title="QueueLess", page_icon="🕒", layout="centered")


# ----------------------------------------------------------------------------
# Core logic (plain Python, no UI code in here)
# ----------------------------------------------------------------------------
@dataclass
class Ticket:
    id: int
    phone: str
    name: str
    joined: datetime
    priority: bool = False
    status: str = "waiting"  # waiting | serving | done | cancelled
    reminded: bool = False


def clean_phone(raw: str):
    """Return a 10-digit Indian mobile number, or None if invalid."""
    digits = re.sub(r"\D", "", raw or "")
    if digits.startswith("91") and len(digits) == 12:
        digits = digits[2:]
    elif digits.startswith("0") and len(digits) == 11:
        digits = digits[1:]
    return digits if re.fullmatch(r"[6-9]\d{9}", digits) else None


class QueueSystem:
    def __init__(self):
        self.lock = threading.RLock()
        self.tickets: list[Ticket] = []
        self.next_id = 1
        self.business_name = "City Clinic"
        self.avg_minutes = 5      # average time to serve one person
        self.reminder_lead = 10   # send "come back" reminder this many minutes before turn
        self.outbox: list[str] = []  # simulated SMS log

    # --- queries ---
    def waiting(self):
        """Waiting tickets in serving order: priority first, then first-come-first-served."""
        w = [t for t in self.tickets if t.status == "waiting"]
        return sorted(w, key=lambda t: (not t.priority, t.joined))

    def serving(self):
        return next((t for t in self.tickets if t.status == "serving"), None)

    def get(self, ticket_id):
        return next((t for t in self.tickets if t.id == ticket_id), None)

    def find_active(self, phone):
        return next(
            (t for t in self.tickets if t.phone == phone and t.status in ("waiting", "serving")),
            None,
        )

    def position(self, ticket):
        """1-based position in the waiting line (None if not waiting)."""
        w = self.waiting()
        return w.index(ticket) + 1 if ticket in w else None

    def wait_minutes(self, ticket):
        pos = self.position(ticket)
        if pos is None:
            return 0
        busy = self.serving() is not None
        return (pos - 1) * self.avg_minutes + (self.avg_minutes if busy else 0)

    # --- actions ---
    def join(self, phone_raw, name="", priority=False):
        with self.lock:
            phone = clean_phone(phone_raw)
            if not phone:
                return None, "Enter a valid 10-digit Indian mobile number."
            if self.find_active(phone):
                return None, "This number is already in the queue."
            t = Ticket(self.next_id, phone, name.strip() or "Guest", datetime.now(), priority)
            self.next_id += 1
            self.tickets.append(t)
            return t, None

    def cancel(self, ticket_id):
        with self.lock:
            t = self.get(ticket_id)
            if t and t.status in ("waiting", "serving"):
                t.status = "cancelled"

    def call_next(self):
        with self.lock:
            current = self.serving()
            if current:
                current.status = "done"
            w = self.waiting()
            if w:
                w[0].status = "serving"
                self.notify(w[0], "It's your turn now. Please come to the counter.")
                return w[0]
        return None

    def toggle_priority(self, ticket_id):
        with self.lock:
            t = self.get(ticket_id)
            if t:
                t.priority = not t.priority

    def notify(self, ticket, message):
        """Simulated SMS. Swap this body for Twilio / MSG91 to send real messages."""
        stamp = datetime.now().strftime("%H:%M:%S")
        self.outbox.append(f"[{stamp}] SMS to +91 {ticket.phone}: {message}")

    def process_reminders(self):
        """Auto-send a reminder once someone is about `reminder_lead` minutes away."""
        with self.lock:
            for t in self.waiting():
                if not t.reminded and self.wait_minutes(t) <= self.reminder_lead:
                    t.reminded = True
                    self.notify(
                        t,
                        f"{self.business_name}: your turn is in about "
                        f"{self.wait_minutes(t)} min. Please head back now.",
                    )


@st.cache_resource
def get_system() -> QueueSystem:
    """One shared queue for every visitor (lives as long as the server process)."""
    return QueueSystem()


qs = get_system()


def fmt_wait(minutes: int) -> str:
    return "Your turn is next" if minutes <= 0 else f"~{minutes} min"


# ----------------------------------------------------------------------------
# Customer interface
# ----------------------------------------------------------------------------
def customer_view():
    st.title(f"🕒 {qs.business_name}")
    st.caption("Join the queue from your phone. No app, no sign-up.")

    ticket = qs.get(st.session_state.get("ticket_id"))
    active = ticket is not None and ticket.status in ("waiting", "serving")

    if not active:
        if ticket is not None and ticket.status == "cancelled":
            st.info("You left the queue.")
        elif ticket is not None and ticket.status == "done":
            st.success("You've been served. Thank you!")

        with st.form("join"):
            phone = st.text_input("Mobile number", placeholder="98765 43210")
            name = st.text_input("Name (optional)")
            joined = st.form_submit_button("Join queue", type="primary")
        if joined:
            t, err = qs.join(phone, name)
            if err:
                st.error(err)
                # If already queued, let them re-attach to their ticket
                existing = qs.find_active(clean_phone(phone) or "")
                if existing:
                    st.session_state.ticket_id = existing.id
                    st.rerun()
            else:
                st.session_state.ticket_id = t.id
                st.rerun()
        return

    live_ticket_panel()


@st.fragment(run_every=5)  # refreshes itself every 5 seconds
def live_ticket_panel():
    qs.process_reminders()
    t = qs.get(st.session_state.get("ticket_id"))
    if t is None or t.status not in ("waiting", "serving"):
        st.rerun(scope="app")
        return

    if t.status == "serving":
        st.success("🎉 It's your turn! Please go to the counter.")
    else:
        pos = qs.position(t)
        wait = qs.wait_minutes(t)
        c1, c2, c3 = st.columns(3)
        c1.metric("Your token", f"#{t.id}")
        c2.metric("People ahead", pos - 1)
        c3.metric("Est. wait", fmt_wait(wait))
        if wait <= qs.reminder_lead:
            st.warning("⏰ Your turn is coming up. Please head back now!")
        else:
            st.info(
                f"You can step away. We'll text +91 {t.phone} when you're "
                f"about {qs.reminder_lead} minutes from your turn."
            )
        if t.priority:
            st.caption("⭐ You've been marked priority.")

    if st.button("Leave queue"):
        qs.cancel(t.id)
        st.rerun(scope="app")


# ----------------------------------------------------------------------------
# Business interface
# ----------------------------------------------------------------------------
def business_view():
    st.title("🗂️ Business dashboard")
    business_panel()


@st.fragment(run_every=5)
def business_panel():
    qs.process_reminders()

    with st.expander("Settings"):
        qs.business_name = st.text_input("Business name", qs.business_name)
        qs.avg_minutes = st.number_input("Avg. minutes per person", 1, 120, qs.avg_minutes)
        qs.reminder_lead = st.number_input("Remind customers this many minutes ahead", 1, 60, qs.reminder_lead)

    waiting = qs.waiting()
    current = qs.serving()

    m1, m2 = st.columns(2)
    m1.metric("Waiting", len(waiting))
    m2.metric("Now serving", f"#{current.id} {current.name}" if current else "—")

    if st.button("▶ Call next", type="primary"):
        if not qs.call_next():
            st.toast("Queue is empty.")
        st.rerun(scope="app")

    with st.form("walkin", clear_on_submit=True):
        st.write("**Add a person**")
        a, b, c = st.columns([3, 3, 2])
        phone = a.text_input("Mobile number")
        name = b.text_input("Name (optional)")
        prio = c.checkbox("Priority")
        if st.form_submit_button("Add to queue"):
            _, err = qs.join(phone, name, prio)
            if err:
                st.error(err)
            else:
                st.rerun(scope="app")

    st.subheader("Queue")
    if not waiting:
        st.write("No one is waiting.")
    for i, t in enumerate(waiting, start=1):
        c1, c2, c3, c4, c5 = st.columns([1, 4, 2, 1, 1])
        c1.write(f"**{i}**")
        star = "⭐ " if t.priority else ""
        c2.write(f"{star}{t.name}  \n+91 {t.phone}")
        c3.write(f"{fmt_wait(qs.wait_minutes(t))}  \nsince {t.joined:%H:%M}")
        if c4.button("⭐", key=f"p{t.id}", help="Toggle priority"):
            qs.toggle_priority(t.id)
            st.rerun(scope="app")
        if c5.button("✖", key=f"x{t.id}", help="Remove"):
            qs.cancel(t.id)
            st.rerun(scope="app")

        # Manual contact button
        if st.button("Send reminder SMS", key=f"n{t.id}"):
            qs.notify(t, f"{qs.business_name}: please be ready, your turn is coming up.")
            st.toast(f"Reminder sent to {t.name}")

    with st.expander("SMS outbox (simulated)"):
        if qs.outbox:
            for line in reversed(qs.outbox[-20:]):
                st.text(line)
        else:
            st.write("No messages yet.")


# ----------------------------------------------------------------------------
# Router
# ----------------------------------------------------------------------------
mode = st.sidebar.radio("Interface", ["Customer", "Business"])
st.sidebar.caption("Open the Customer view on one device and the Business view on another to see it live.")

if mode == "Customer":
    customer_view()
else:
    business_view()
