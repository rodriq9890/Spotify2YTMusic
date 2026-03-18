"""Simple colored terminal logger."""

RESET  = "\033[0m"
GREEN  = "\033[92m"
YELLOW = "\033[93m"
RED    = "\033[91m"
CYAN   = "\033[96m"
BOLD   = "\033[1m"


def log(msg: str):
    print(msg)

def log_success(msg: str):
    print(f"{GREEN}{msg}{RESET}")

def log_warn(msg: str):
    print(f"{YELLOW}{msg}{RESET}")

def log_error(msg: str):
    print(f"{RED}{msg}{RESET}")

def log_section(title: str):
    print(f"\n{BOLD}{CYAN}{'─' * 50}{RESET}")
    print(f"{BOLD}{CYAN}  {title}{RESET}")
    print(f"{BOLD}{CYAN}{'─' * 50}{RESET}")