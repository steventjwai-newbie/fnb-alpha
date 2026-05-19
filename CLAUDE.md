# Project: F&B Alpha — Cafe Operations System

## Scope
Operate only within this folder and its subfolders.
Never read or write outside this directory.

## Tech Stack
- Python 3.11+
- Seatable Python API
- Gemini API for vision/OCR
- python-telegram-bot for Telegram interface

## Critical Safety Rules
- NEVER write to Seatable's Supplier Products table without explicit /yes from Telegram
- NEVER commit secrets — they're in .gitignore
- ALWAYS log every Seatable write to Price History
- ALWAYS use structured JSON output schemas

## Workflow
- Plan mode (Shift+Tab) before executing
- Commit after every working feature
- One Telegram command = one skill = one file
