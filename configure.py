#!/usr/bin/env python3
"""
BrijjData configuration manager.
Run this script to create or update config.ini without editing it by hand.
"""

import configparser
import os
import sys

CONFIG_FILE = os.path.join(os.path.dirname(__file__), "config.ini")


# ── Helpers ───────────────────────────────────────────────────────────────────

def load_config() -> configparser.ConfigParser:
    cfg = configparser.ConfigParser()
    if os.path.exists(CONFIG_FILE):
        cfg.read(CONFIG_FILE)
    return cfg


def save_config(cfg: configparser.ConfigParser):
    with open(CONFIG_FILE, "w") as f:
        cfg.write(f)
    print(f"\n  Saved to {CONFIG_FILE}")


def prompt(label: str, current: str = "", secret: bool = False) -> str:
    """Prompt for a value, showing the current value as default."""
    if secret and current:
        display = f"[{'*' * min(len(current), 8)}]"
    elif current:
        display = f"[{current}]"
    else:
        display = ""
    hint = f" {display}" if display else ""
    value = input(f"  {label}{hint}: ").strip()
    return value if value else current


def hr(char="─", width=50):
    print(char * width)


def company_sections(cfg: configparser.ConfigParser) -> list:
    return [s for s in cfg.sections() if s.startswith("company:")]


# ── Menu sections ─────────────────────────────────────────────────────────────

def edit_database(cfg: configparser.ConfigParser):
    hr()
    print("  DATABASE SETTINGS")
    hr()
    if not cfg.has_section("database"):
        cfg.add_section("database")
    sec = cfg["database"]
    sec["server"]   = prompt("Server / hostname", sec.get("server", ""))
    sec["port"]     = prompt("Port",              sec.get("port", "1433"))
    sec["user"]     = prompt("Username",          sec.get("user", ""))
    sec["password"] = prompt("Password",          sec.get("password", ""), secret=True)
    sec["name"]     = prompt("Database name",     sec.get("name", "BrijjData"))
    save_config(cfg)


def edit_apis(cfg: configparser.ConfigParser):
    hr()
    print("  API ENDPOINTS")
    hr()
    if not cfg.has_section("apis"):
        cfg.add_section("apis")
    sec = cfg["apis"]
    sec["rcs_base"]        = prompt("RCS base URL",        sec.get("rcs_base",   "https://productionsystem-api.brijjworks.com"))
    sec["auth_base"]       = prompt("Auth base URL",       sec.get("auth_base",  "https://company-api.brijjworks.com/api"))
    sec["sales_base"]      = prompt("Sales base URL",      sec.get("sales_base", "https://pos-api.brijjworks.com/api"))
    sec["sales_page_size"] = prompt("Sales page size",     sec.get("sales_page_size", "1000"))
    save_config(cfg)


def list_companies(cfg: configparser.ConfigParser):
    sections = company_sections(cfg)
    if not sections:
        print("  (no companies configured)")
        return
    print(f"  {'ID':<12} {'Username':<25} {'Int ID':<8} Enabled")
    hr()
    for s in sections:
        sec = cfg[s]
        print(f"  {sec.get('company_id',''):<12} {sec.get('username',''):<25} {sec.get('company_int_id',''):<8} {sec.get('enabled','false')}")


def edit_company(cfg: configparser.ConfigParser, section: str):
    if not cfg.has_section(section):
        cfg.add_section(section)
    sec = cfg[section]
    cid = section.split(":", 1)[1]
    hr()
    print(f"  COMPANY: {cid}")
    hr()
    sec["company_id"]     = prompt("Company ID (external)",  sec.get("company_id", cid))
    sec["company_int_id"] = prompt("Company int ID (numeric, from auth)", sec.get("company_int_id", ""))
    sec["username"]       = prompt("API username",           sec.get("username", ""))
    sec["password"]       = prompt("API password",           sec.get("password", ""), secret=True)
    enabled = prompt("Enabled (true/false)", sec.get("enabled", "true"))
    sec["enabled"]        = "true" if enabled.lower() in ("true", "yes", "1", "y") else "false"
    save_config(cfg)


def manage_companies(cfg: configparser.ConfigParser):
    while True:
        hr()
        print("  MANAGE COMPANIES")
        hr()
        list_companies(cfg)
        print()
        print("  a) Add / edit a company")
        print("  d) Disable a company")
        print("  r) Remove a company")
        print("  b) Back")
        choice = input("\n  Choice: ").strip().lower()

        if choice == "a":
            cid = input("  Company ID (e.g. VD001): ").strip().upper()
            if not cid:
                continue
            edit_company(cfg, f"company:{cid}")

        elif choice == "d":
            cid = input("  Company ID to disable: ").strip().upper()
            section = f"company:{cid}"
            if cfg.has_section(section):
                cfg[section]["enabled"] = "false"
                save_config(cfg)
                print(f"  {cid} disabled.")
            else:
                print(f"  Company '{cid}' not found.")

        elif choice == "r":
            cid = input("  Company ID to remove: ").strip().upper()
            section = f"company:{cid}"
            if cfg.has_section(section):
                confirm = input(f"  Remove {cid}? (yes/no): ").strip().lower()
                if confirm == "yes":
                    cfg.remove_section(section)
                    save_config(cfg)
                    print(f"  {cid} removed.")
            else:
                print(f"  Company '{cid}' not found.")

        elif choice == "b":
            break


def show_summary(cfg: configparser.ConfigParser):
    hr()
    print("  CURRENT CONFIGURATION SUMMARY")
    hr()

    if cfg.has_section("database"):
        db = cfg["database"]
        print(f"  Database : {db.get('name')} on {db.get('server')}:{db.get('port')}")
        print(f"  DB User  : {db.get('user')}")
    else:
        print("  Database : (not configured)")

    print()

    if cfg.has_section("apis"):
        apis = cfg["apis"]
        print(f"  RCS API  : {apis.get('rcs_base')}")
        print(f"  Auth API : {apis.get('auth_base')}")
        print(f"  Sales API: {apis.get('sales_base')}")
    else:
        print("  APIs     : (not configured)")

    print()
    print("  Companies:")
    list_companies(cfg)
    hr()


# ── Main menu ─────────────────────────────────────────────────────────────────

def main():
    cfg = load_config()

    if not os.path.exists(CONFIG_FILE):
        print(f"\n  No config file found. A new one will be created at:\n  {CONFIG_FILE}\n")

    while True:
        print()
        hr("═")
        print("  BRIJJDATA CONFIGURATION")
        hr("═")
        print("  1) Database settings")
        print("  2) API endpoints")
        print("  3) Manage companies")
        print("  4) Show current config")
        print("  5) Exit")
        hr()
        choice = input("  Choice: ").strip()

        if choice == "1":
            edit_database(cfg)
        elif choice == "2":
            edit_apis(cfg)
        elif choice == "3":
            manage_companies(cfg)
        elif choice == "4":
            show_summary(cfg)
        elif choice == "5":
            print()
            sys.exit(0)
        else:
            print("  Invalid choice.")


if __name__ == "__main__":
    main()
