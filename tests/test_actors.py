"""P2.7: Aktoren — Kopf-Syntax, receive, MailboxPolicy, ->>, Scheduler (§9)."""

import pytest

from marla.checker import check
from marla.cli import compile_source
from marla.errors import CompileError
from marla.lexer import lex
from marla.parser import parse

from test_codegen import run_on_host


MOTOR = (
    "enum MotorMsg:\n"
    "    Stopp\n"
    "    SetSpeed(rpm: u16)\n"
    "\n"
    "extern fn hal_delay_ms(ms: u16)\n"
    "\n"
    "actor Motor(mailbox: 8, full: DropOldest):\n"
    "    current_rpm: u16 := 0\n"
    "\n"
    "    fn receive(msg: MotorMsg):\n"
    "        match msg:\n"
    "            Stopp:\n"
    "                self.current_rpm := 0\n"
    "                hal_delay_ms(0)\n"
    "            SetSpeed(rpm):\n"
    "                self.current_rpm := rpm\n"
    "                hal_delay_ms(rpm)\n"
    "\n"
)


def run(source):
    return check(parse(lex(source)))


def ok(source):
    run(source)


def bad(source, fragment):
    with pytest.raises(CompileError) as exc:
        run(source)
    assert fragment in exc.value.message, exc.value.message


# --- Checker -----------------------------------------------------------------------

def test_aktor_grundform():
    ok(MOTOR +
       "fn main():\n"
       "    SetSpeed(120) ->> Motor\n"
       "    Stopp ->> Motor\n")


def test_kopf_beide_optionen_pflicht():
    bad(MOTOR.replace("(mailbox: 8, full: DropOldest)", "(mailbox: 8)")
        + "fn main():\n    return\n",
        "'full' fehlt")
    bad(MOTOR.replace("(mailbox: 8, full: DropOldest)", "(full: Drop)")
        + "fn main():\n    return\n",
        "'mailbox' fehlt")


def test_kopf_unbekannte_option_mit_hinweis():
    bad(MOTOR.replace("mailbox: 8", "mailbox2: 8")
        + "fn main():\n    return\n",
        "unbekannte Aktor-Option 'mailbox2'")


def test_kopf_policy_muss_mailboxpolicy_sein():
    bad(MOTOR.replace("full: DropOldest", "full: Stopp")
        + "fn main():\n    return\n",
        "MailboxPolicy")


def test_kopf_mailbox_konstant():
    ok("const TIEFE: u8 := 4\n"
       + MOTOR.replace("mailbox: 8", "mailbox: TIEFE")
       + "fn main():\n    return\n")


def test_receive_pflicht():
    bad("enum M:\n    X\n"
        "actor A(mailbox: 2, full: Drop):\n"
        "    n: u8 := 0\n"
        "fn main():\n    return\n",
        "fn receive")


def test_receive_ohne_self():
    bad("enum M:\n    X\n"
        "actor A(mailbox: 2, full: Drop):\n"
        "    fn receive(self, msg: M):\n"
        "        return\n"
        "fn main():\n    return\n",
        "implizit")


def test_zustand_braucht_startwert():
    bad("enum M:\n    X\n"
        "actor A(mailbox: 2, full: Drop):\n"
        "    n: u8\n"
        "    fn receive(msg: M):\n"
        "        return\n"
        "fn main():\n    return\n",
        "Startwert")


def test_isolation_zustand_nur_im_aktor():
    bad(MOTOR +
        "fn main():\n"
        "    let x := Motor\n",
        "isoliert")


def test_send_typpruefung():
    bad(MOTOR +
        "enum Anderes:\n"
        "    Foo\n"
        "fn main():\n"
        "    Foo ->> Motor\n",
        "erwartet 'MotorMsg'")


def test_send_unbekannter_aktor():
    bad("enum M:\n    X\nfn main():\n    X ->> Nirgendwo\n",
        "unbekannter Aktor")


def test_sende_aus_funktion_und_aktor():
    ok(MOTOR +
       "fn stoppe_alles():\n"
       "    Stopp ->> Motor\n"
       "fn main():\n"
       "    stoppe_alles()\n")


# --- Codegen -----------------------------------------------------------------------

def test_c_infrastruktur():
    c = compile_source(MOTOR + "fn main():\n    Stopp ->> Motor\n", "a.mar")
    assert "static ml_actor_Motor_state ml_actor_Motor" in c
    assert "static ml_MotorMsg ml__mbox_Motor[8];" in c
    assert "static void ml__send_Motor(ml_MotorMsg m)" in c
    assert "ML_IRQ_LOCK();" in c
    assert "ml_Motor_receive(&ml_actor_Motor, &m);" in c
    assert "ml__poll();" in c


def test_aktor_laeuft_auf_host(tmp_path):
    out = run_on_host(tmp_path, (
        MOTOR +
        "fn main():\n"
        "    SetSpeed(120) ->> Motor\n"
        "    SetSpeed(50) ->> Motor\n"
        "    Stopp ->> Motor\n"
    ))
    # Nachrichten werden nach main() der Reihe nach verarbeitet
    assert out == "  warte 120 ms\n  warte 50 ms\n  warte 0 ms\n"


def test_drop_oldest_policy(tmp_path):
    out = run_on_host(tmp_path, (
        "enum M:\n"
        "    Wert(v: u16)\n"
        "extern fn hal_delay_ms(ms: u16)\n"
        "actor Sammler(mailbox: 2, full: DropOldest):\n"
        "    fn receive(msg: M):\n"
        "        match msg:\n"
        "            Wert(v):\n"
        "                hal_delay_ms(v)\n"
        "fn main():\n"
        "    Wert(1) ->> Sammler\n"
        "    Wert(2) ->> Sammler\n"
        "    Wert(3) ->> Sammler\n"     # Mailbox voll -> 1 fliegt raus
    ))
    assert out == "  warte 2 ms\n  warte 3 ms\n"


def test_drop_policy(tmp_path):
    out = run_on_host(tmp_path, (
        "enum M:\n"
        "    Wert(v: u16)\n"
        "extern fn hal_delay_ms(ms: u16)\n"
        "actor Sammler(mailbox: 2, full: Drop):\n"
        "    fn receive(msg: M):\n"
        "        match msg:\n"
        "            Wert(v):\n"
        "                hal_delay_ms(v)\n"
        "fn main():\n"
        "    Wert(1) ->> Sammler\n"
        "    Wert(2) ->> Sammler\n"
        "    Wert(3) ->> Sammler\n"     # Mailbox voll -> 3 wird verworfen
    ))
    assert out == "  warte 1 ms\n  warte 2 ms\n"


def test_panic_policy(tmp_path):
    out = run_on_host(tmp_path, (
        "enum M:\n"
        "    Wert(v: u16)\n"
        "extern fn hal_delay_ms(ms: u16)\n"
        "actor Streng(mailbox: 1, full: Panic):\n"
        "    fn receive(msg: M):\n"
        "        match msg:\n"
        "            Wert(v):\n"
        "                hal_delay_ms(v)\n"
        "fn main():\n"
        "    Wert(1) ->> Streng\n"
        "    Wert(2) ->> Streng\n"     # Überlauf = Programmfehler -> PANIC
    ))
    assert out == ""
    assert "PANIC" in run_on_host.last_stderr


def test_aktor_selbstnachricht_und_zustand(tmp_path):
    # Aktor schickt sich selbst Nachrichten: Countdown über die Mailbox
    out = run_on_host(tmp_path, (
        "enum Tick:\n"
        "    Weiter(rest: u16)\n"
        "extern fn hal_delay_ms(ms: u16)\n"
        "actor Zaehler(mailbox: 4, full: Panic):\n"
        "    summe: u16 := 0\n"
        "    fn receive(msg: Tick):\n"
        "        match msg:\n"
        "            Weiter(rest):\n"
        "                self.summe += rest\n"
        "                if rest > 0:\n"
        "                    Weiter(rest - 1) ->> Zaehler\n"
        "                else:\n"
        "                    hal_delay_ms(self.summe)\n"
        "fn main():\n"
        "    Weiter(4) ->> Zaehler\n"   # 4+3+2+1+0 = 10
    ))
    assert out == "  warte 10 ms\n"
