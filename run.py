#!/usr/bin/env python3
"""Запуск: python run.py [--host 0.0.0.0] [--port 8000]"""
import argparse

import uvicorn

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    args = ap.parse_args()
    uvicorn.run("backend.main:app", host=args.host, port=args.port, log_level="info")
