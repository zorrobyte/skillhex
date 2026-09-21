#!/usr/bin/env python3
"""Tiny notes CLI used as a SkillHEX fixture.

Active notes live in notes.json. Archived notes are moved to archive/notes.json
and are NOT shown by `list` unless --include-archived is given.
"""
import argparse, json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))


def load(path):
    try:
        with open(path) as f:
            return json.load(f)
    except FileNotFoundError:
        return []


def main():
    ap = argparse.ArgumentParser(prog="notes.py")
    sub = ap.add_subparsers(dest="cmd", required=True)
    ls = sub.add_parser("list", help="list active notes")
    ls.add_argument("--include-archived", action="store_true", help="also list notes that were archived")
    ls.add_argument("--json", action="store_true")
    sub.add_parser("add").add_argument("text")
    a = ap.parse_args()
    if a.cmd == "list":
        notes = load(os.path.join(HERE, "notes.json"))
        if a.include_archived:
            notes = notes + [dict(n, archived=True) for n in load(os.path.join(HERE, "archive", "notes.json"))]
        if a.json:
            print(json.dumps(notes))
        else:
            for n in notes:
                print(f"[{n['id']}] {n['text']}" + (" (archived)" if n.get("archived") else ""))
    elif a.cmd == "add":
        p = os.path.join(HERE, "notes.json")
        notes = load(p)
        notes.append({"id": max([n["id"] for n in notes] + [0]) + 1, "text": a.text})
        json.dump(notes, open(p, "w"))
        print("added")


if __name__ == "__main__":
    main()
