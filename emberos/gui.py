"""EmberOS-Windows full GUI window using Tkinter."""

import json
import logging
import os
import queue
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext

import psutil
import requests

ROOT_DIR = Path(__file__).resolve().parent.parent
logger = logging.getLogger("emberos.gui")

# ── Theme Definitions ────────────────────────────────────────────

THEMES = {
    "dark": {
        "bg": "#1a1a2e",
        "bg2": "#16213e",
        "accent": "#e94560",
        "text": "#eaeaea",
        "user_bubble": "#0f3460",
        "user_text": "#ffffff",
        "agent_bubble": "#1a1a2e",
        "agent_text": "#eaeaea",
        "btn_bg": "#e94560",
        "btn_text": "#ffffff",
        "ribbon_bg": "#16213e",
        "status_bg": "#111122",
        "input_bg": "#16213e",
        "input_text": "#eaeaea",
        "placeholder": "#666688",
        "border": "#2a2a4e",
    },
    "light": {
        "bg": "#f5f5f5",
        "bg2": "#ffffff",
        "accent": "#e94560",
        "text": "#1a1a2e",
        "user_bubble": "#0f3460",
        "user_text": "#ffffff",
        "agent_bubble": "#e8e8e8",
        "agent_text": "#1a1a2e",
        "btn_bg": "#e94560",
        "btn_text": "#ffffff",
        "ribbon_bg": "#e0e0e0",
        "status_bg": "#d5d5d5",
        "input_bg": "#ffffff",
        "input_text": "#1a1a2e",
        "placeholder": "#999999",
        "border": "#cccccc",
    },
}


def _load_api_base() -> str:
    try:
        cfg_path = ROOT_DIR / "config" / "default.json"
        if cfg_path.exists():
            with open(cfg_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return f"http://{data.get('server_host', '127.0.0.1')}:{data.get('agent_api_port', 8766)}"
    except Exception:
        pass
    return "http://127.0.0.1:8766"


def _load_config_value(key, default=None):
    try:
        cfg_path = ROOT_DIR / "config" / "default.json"
        if cfg_path.exists():
            with open(cfg_path, "r", encoding="utf-8") as f:
                return json.load(f).get(key, default)
    except Exception:
        pass
    return default


def _save_config_value(key, value):
    try:
        cfg_path = ROOT_DIR / "config" / "default.json"
        data = {}
        if cfg_path.exists():
            with open(cfg_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        data[key] = value
        with open(cfg_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except Exception:
        pass


class EmberGUI:
    """Main EmberOS GUI window."""

    def __init__(self):
        self.api_base = _load_api_base()
        self.theme_name = _load_config_value("theme", "dark")
        self.theme = THEMES.get(self.theme_name, THEMES["dark"])
        self.attached_files: list[str] = []
        self.response_queue: queue.Queue = queue.Queue()
        self.maximized = False
        self._drag_x = 0
        self._drag_y = 0
        self.last_tool = ""
        self.agent_status = "Ready"

        self.root = tk.Tk()
        self.root.overrideredirect(True)
        self.root.title("EmberOS")

        # Restore geometry
        geo = _load_config_value("gui_geometry", "900x700+100+100")
        self.root.geometry(geo)
        self.root.minsize(600, 450)

        self._build_ui()
        self.apply_theme(self.theme_name)

        # Keyboard shortcuts
        self.root.bind("<Control-q>", lambda e: self._on_close())

        # Start update loops
        self.root.after(100, self._poll_responses)
        self.root.after(2000, self._update_ribbon)
        self.root.after(3000, self._update_status_bar)
        self.root.after(5000, self._poll_snapshot_state)

    # ── UI Construction ──────────────────────────────────────────

    def _build_ui(self):
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(2, weight=1)  # chat area row

        self._build_title_bar()
        self._build_ribbon()
        self._build_chat_area()
        self._build_input_area()
        self._build_status_bar()

    def _build_title_bar(self):
        self.title_bar = tk.Frame(self.root, height=36)
        self.title_bar.grid(row=0, column=0, sticky="ew")
        self.title_bar.columnconfigure(1, weight=1)

        # Logo + title
        self.logo_label = tk.Label(self.title_bar, text="\U0001F525 EmberOS",
                                   font=("Segoe UI", 11, "bold"), padx=10)
        self.logo_label.grid(row=0, column=0, sticky="w")

        # Spacer
        spacer = tk.Label(self.title_bar, text="")
        spacer.grid(row=0, column=1, sticky="ew")

        # Buttons
        btn_frame = tk.Frame(self.title_bar)
        btn_frame.grid(row=0, column=2, sticky="e", padx=4)

        self.theme_btn = tk.Button(btn_frame, text="Theme", width=6, bd=0,
                                   command=self._toggle_theme)
        self.theme_btn.pack(side=tk.LEFT, padx=2)

        self.min_btn = tk.Button(btn_frame, text="\u2014", width=3, bd=0,
                                 command=lambda: self.root.iconify())
        self.min_btn.pack(side=tk.LEFT, padx=1)

        self.max_btn = tk.Button(btn_frame, text="\u25A1", width=3, bd=0,
                                 command=self._toggle_maximize)
        self.max_btn.pack(side=tk.LEFT, padx=1)

        self.close_btn = tk.Button(btn_frame, text="\u2715", width=3, bd=0,
                                   command=self._on_close)
        self.close_btn.pack(side=tk.LEFT, padx=1)

        # Dragging
        for w in (self.title_bar, self.logo_label, spacer):
            w.bind("<Button-1>", self._start_drag)
            w.bind("<B1-Motion>", self._on_drag)
            w.bind("<Double-Button-1>", lambda e: self._toggle_maximize())

    def _build_ribbon(self):
        self.ribbon = tk.Frame(self.root, height=24)
        self.ribbon.grid(row=1, column=0, sticky="ew")

        self.ribbon_active = tk.Label(self.ribbon, text="Active App: ...",
                                      font=("Segoe UI", 9), anchor="w", padx=8)
        self.ribbon_active.pack(side=tk.LEFT, fill=tk.X, expand=True)

        sep1 = tk.Label(self.ribbon, text="|", font=("Segoe UI", 9))
        sep1.pack(side=tk.LEFT)

        self.ribbon_tool = tk.Label(self.ribbon, text="No active tools",
                                    font=("Segoe UI", 9), padx=8)
        self.ribbon_tool.pack(side=tk.LEFT)

        sep2 = tk.Label(self.ribbon, text="|", font=("Segoe UI", 9))
        sep2.pack(side=tk.LEFT)

        self.ribbon_status = tk.Label(self.ribbon, text="Status: Ready",
                                      font=("Segoe UI", 9), padx=8)
        self.ribbon_status.pack(side=tk.LEFT)

    def _build_chat_area(self):
        self.chat = scrolledtext.ScrolledText(
            self.root, wrap=tk.WORD, state=tk.DISABLED,
            font=("Consolas", 11), bd=0, padx=8, pady=8,
        )
        self.chat.grid(row=2, column=0, sticky="nsew", padx=4, pady=2)

        # Tags for message styling
        self.chat.tag_configure("user", justify="right", foreground="#ffffff",
                                background="#0f3460", lmargin1=100, rmargin=8,
                                spacing1=4, spacing3=4)
        self.chat.tag_configure("agent", justify="left", foreground="#eaeaea",
                                background="#1a1a2e", lmargin1=8, rmargin=100,
                                spacing1=4, spacing3=4)
        self.chat.tag_configure("system", justify="center",
                                foreground="#888888", font=("Consolas", 9),
                                spacing1=2, spacing3=2)

    def _build_input_area(self):
        input_container = tk.Frame(self.root)
        input_container.grid(row=3, column=0, sticky="ew", padx=4, pady=2)
        input_container.columnconfigure(0, weight=1)

        # Attachment row
        attach_frame = tk.Frame(input_container)
        attach_frame.grid(row=0, column=0, sticky="ew", pady=(0, 2))

        self.attach_btn = tk.Button(attach_frame, text="\U0001F4CE Attach Files",
                                    bd=0, command=self._attach_files)
        self.attach_btn.pack(side=tk.LEFT, padx=4)

        self.attach_label = tk.Label(attach_frame, text="", font=("Segoe UI", 9))
        self.attach_label.pack(side=tk.LEFT, padx=4)

        # Text input
        self.input_box = tk.Text(input_container, height=4, wrap=tk.WORD,
                                 font=("Consolas", 11), bd=1, relief=tk.SOLID)
        self.input_box.grid(row=1, column=0, sticky="ew", padx=2)

        # Placeholder
        self._placeholder_active = True
        self.input_box.insert("1.0", "Type your message here...")
        self.input_box.bind("<FocusIn>", self._on_input_focus_in)
        self.input_box.bind("<FocusOut>", self._on_input_focus_out)
        self.input_box.bind("<Return>", self._on_enter)
        self.input_box.bind("<Shift-Return>", self._on_shift_enter)
        self.input_box.bind("<Escape>", self._on_escape)

        # Button row
        btn_row = tk.Frame(input_container)
        btn_row.grid(row=2, column=0, sticky="ew", pady=(4, 0))

        # Left side — task control
        left_btns = tk.Frame(btn_row)
        left_btns.pack(side=tk.LEFT)

        self.interrupt_btn = tk.Button(left_btns, text="\u23F8 Interrupt", bd=0,
                                       state=tk.DISABLED, command=self._on_interrupt)
        self.interrupt_btn.pack(side=tk.LEFT, padx=2)

        self.rollback_btn = tk.Button(left_btns, text="\u21A9 Rollback", bd=0,
                                      state=tk.DISABLED, command=self._on_rollback)
        self.rollback_btn.pack(side=tk.LEFT, padx=2)

        self.cancel_btn = tk.Button(left_btns, text="Cancel", bd=0,
                                    command=self._on_cancel)
        self.cancel_btn.pack(side=tk.LEFT, padx=2)

        # Right side — action
        right_btns = tk.Frame(btn_row)
        right_btns.pack(side=tk.RIGHT)

        self.execute_btn = tk.Button(right_btns, text="Execute", bd=0,
                                     command=self._on_execute)
        self.execute_btn.pack(side=tk.LEFT, padx=2)

        self.send_btn = tk.Button(right_btns, text="Send \u26A1", bd=0,
                                  command=self._send_message)
        self.send_btn.pack(side=tk.LEFT, padx=2)

    def _build_status_bar(self):
        self.status_bar = tk.Frame(self.root, height=22)
        self.status_bar.grid(row=4, column=0, sticky="ew")

        self.status_label = tk.Label(self.status_bar, text="Status: Ready",
                                     font=("Segoe UI", 9), anchor="w", padx=8)
        self.status_label.pack(fill=tk.X)

    # ── Theme Management ─────────────────────────────────────────

    def apply_theme(self, theme_name: str):
        t = THEMES.get(theme_name, THEMES["dark"])
        self.theme = t
        self.theme_name = theme_name

        # Root
        self.root.configure(bg=t["bg"])

        # Title bar
        for w in (self.title_bar, self.logo_label):
            w.configure(bg=t["bg2"])
        self.logo_label.configure(fg=t["text"])
        for btn in (self.theme_btn, self.min_btn, self.max_btn):
            btn.configure(bg=t["btn_bg"], fg=t["btn_text"], activebackground=t["accent"])
        self.close_btn.configure(bg="#c0392b", fg="#ffffff", activebackground="#e74c3c")

        # Recolor title bar spacer and button frame
        for child in self.title_bar.winfo_children():
            if isinstance(child, tk.Frame):
                child.configure(bg=t["bg2"])
            elif isinstance(child, tk.Label):
                child.configure(bg=t["bg2"], fg=t["text"])

        # Ribbon
        self.ribbon.configure(bg=t["ribbon_bg"])
        for w in (self.ribbon_active, self.ribbon_tool, self.ribbon_status):
            w.configure(bg=t["ribbon_bg"], fg=t["text"])
        for child in self.ribbon.winfo_children():
            child.configure(bg=t["ribbon_bg"])
            if isinstance(child, tk.Label):
                child.configure(fg=t["text"])

        # Chat area
        self.chat.configure(bg=t["bg2"], fg=t["text"], insertbackground=t["text"])
        self.chat.tag_configure("user", foreground=t["user_text"],
                                background=t["user_bubble"])
        self.chat.tag_configure("agent", foreground=t["agent_text"],
                                background=t["agent_bubble"])
        self.chat.tag_configure("system", foreground=t["placeholder"])

        # Input area
        input_container = self.input_box.master
        input_container.configure(bg=t["bg"])
        for child in input_container.winfo_children():
            if isinstance(child, tk.Frame):
                child.configure(bg=t["bg"])

        self.input_box.configure(bg=t["input_bg"], fg=t["input_text"],
                                 insertbackground=t["text"],
                                 highlightbackground=t["border"])
        if self._placeholder_active:
            self.input_box.configure(fg=t["placeholder"])

        # Attach area
        self.attach_btn.configure(bg=t["btn_bg"], fg=t["btn_text"],
                                  activebackground=t["accent"])
        self.attach_label.configure(bg=t["bg"], fg=t["text"])

        # Buttons
        for btn in (self.interrupt_btn, self.rollback_btn, self.cancel_btn,
                     self.execute_btn, self.send_btn):
            btn.configure(bg=t["btn_bg"], fg=t["btn_text"],
                         activebackground=t["accent"],
                         disabledforeground="#666666")

        # Status bar
        self.status_bar.configure(bg=t["status_bg"])
        self.status_label.configure(bg=t["status_bg"], fg=t["text"])

    def _toggle_theme(self):
        new_theme = "light" if self.theme_name == "dark" else "dark"
        self.apply_theme(new_theme)
        _save_config_value("theme", new_theme)

    # ── Drag / Maximize ──────────────────────────────────────────

    def _start_drag(self, event):
        self._drag_x = event.x
        self._drag_y = event.y

    def _on_drag(self, event):
        if self.maximized:
            return
        x = self.root.winfo_x() + event.x - self._drag_x
        y = self.root.winfo_y() + event.y - self._drag_y
        self.root.geometry(f"+{x}+{y}")

    def _toggle_maximize(self):
        if self.maximized:
            self.root.state("normal")
            self.maximized = False
            self.max_btn.configure(text="\u25A1")
        else:
            self.root.state("zoomed")
            self.maximized = True
            self.max_btn.configure(text="\u25A3")

    # ── Input Handling ───────────────────────────────────────────

    def _on_input_focus_in(self, _event):
        if self._placeholder_active:
            self.input_box.delete("1.0", tk.END)
            self.input_box.configure(fg=self.theme["input_text"])
            self._placeholder_active = False

    def _on_input_focus_out(self, _event):
        text = self.input_box.get("1.0", tk.END).strip()
        if not text:
            self._placeholder_active = True
            self.input_box.insert("1.0", "Type your message here...")
            self.input_box.configure(fg=self.theme["placeholder"])

    def _on_enter(self, event):
        self._send_message()
        return "break"

    def _on_shift_enter(self, _event):
        return  # Allow default newline insertion

    def _on_escape(self, _event):
        self.input_box.delete("1.0", tk.END)
        self.attached_files.clear()
        self.attach_label.configure(text="")
        self.attach_btn.configure(text="\U0001F4CE Attach Files")
        self._placeholder_active = True
        self.input_box.insert("1.0", "Type your message here...")
        self.input_box.configure(fg=self.theme["placeholder"])

    def _get_input_text(self) -> str:
        if self._placeholder_active:
            return ""
        return self.input_box.get("1.0", tk.END).strip()

    # ── Attachments ──────────────────────────────────────────────

    def _attach_files(self):
        filetypes = [
            ("Images", "*.png *.jpg *.jpeg *.gif *.bmp *.svg"),
            ("Documents", "*.pdf *.doc *.docx *.txt *.md *.odt"),
            ("Spreadsheets", "*.xls *.xlsx *.csv *.ods"),
            ("Code", "*.py *.js *.java *.cpp *.c *.h *.rs *.go *.ts"),
            ("Archives", "*.zip *.tar *.gz *.bz2 *.7z"),
            ("All files", "*.*"),
        ]
        paths = filedialog.askopenfilenames(title="Attach Files", filetypes=filetypes)
        if paths:
            self.attached_files.extend(paths)
            self.attach_btn.configure(text="\U0001F4CE Add More")
            self.attach_label.configure(text=f"{len(self.attached_files)} files attached")

    # ── Chat Display ─────────────────────────────────────────────

    def _append_chat(self, text: str, tag: str = "agent"):
        self.chat.configure(state=tk.NORMAL)
        self.chat.insert(tk.END, text + "\n", tag)
        self.chat.configure(state=tk.DISABLED)
        self.chat.see(tk.END)

    # ── Send / Execute ───────────────────────────────────────────

    def _send_message(self):
        text = self._get_input_text()
        if not text and not self.attached_files:
            return

        # Build user message with attachments
        if self.attached_files:
            file_info = "\n".join(f"[Attached: {Path(f).name}]" for f in self.attached_files)
            user_msg = f"{text}\n{file_info}" if text else file_info
            send_text = text or "Analyze the attached files"
        else:
            user_msg = text
            send_text = text

        # Show in chat
        ts = datetime.now().strftime("%H:%M")
        self._append_chat(f"[{ts}] You: {user_msg}", "user")

        # Clear input
        self.input_box.delete("1.0", tk.END)
        self._placeholder_active = True
        self.input_box.insert("1.0", "Type your message here...")
        self.input_box.configure(fg=self.theme["placeholder"])

        # Update status
        self.agent_status = "Thinking..."
        self.interrupt_btn.configure(state=tk.NORMAL)

        # Build payload
        payload = {"input": send_text}
        if self.attached_files:
            payload["attached_files"] = list(self.attached_files)
            self.attached_files.clear()
            self.attach_label.configure(text="")
            self.attach_btn.configure(text="\U0001F4CE Attach Files")

        # Send in background
        threading.Thread(target=self._do_query, args=(payload,), daemon=True).start()

    def _on_execute(self):
        text = self._get_input_text()
        if not text:
            return
        if not messagebox.askyesno("Execute Command", f"Run directly: {text}?"):
            return
        ts = datetime.now().strftime("%H:%M")
        self._append_chat(f"[{ts}] You (execute): {text}", "user")
        self.input_box.delete("1.0", tk.END)
        payload = {"input": f'{{"tool": "run_shell", "params": {{"cmd": "{text}"}}}}'}
        threading.Thread(target=self._do_query, args=(payload,), daemon=True).start()

    def _do_query(self, payload: dict):
        try:
            resp = requests.post(
                f"{self.api_base}/query",
                json=payload, timeout=300,
            )
            if resp.status_code == 200:
                answer = resp.json().get("response", "(no response)")
            else:
                answer = f"[Error {resp.status_code}] {resp.text}"
        except requests.ConnectionError:
            answer = "[Error] Cannot connect to EmberOS service. Is it running?"
        except Exception as e:
            answer = f"[Error] {e}"
        self.response_queue.put(answer)

    def _poll_responses(self):
        try:
            while True:
                answer = self.response_queue.get_nowait()
                ts = datetime.now().strftime("%H:%M")
                self._append_chat(f"[{ts}] EmberOS: {answer}", "agent")
                self.agent_status = "Ready"
                self.interrupt_btn.configure(state=tk.DISABLED)
        except queue.Empty:
            pass
        self.root.after(100, self._poll_responses)

    # ── Interrupt / Rollback / Cancel ────────────────────────────

    def _on_interrupt(self):
        try:
            requests.post(f"{self.api_base}/control",
                          json={"action": "interrupt"}, timeout=5)
        except Exception:
            pass
        self._append_chat("[Interrupt requested]", "system")

    def _on_rollback(self):
        try:
            resp = requests.post(f"{self.api_base}/control",
                                 json={"action": "rollback"}, timeout=10)
            if resp.status_code == 200:
                msg = resp.json().get("result", "Rollback complete")
            else:
                msg = f"Rollback failed: {resp.text}"
        except Exception as e:
            msg = f"Rollback error: {e}"
        self._append_chat(f"[Rollback] {msg}", "system")

    def _on_cancel(self):
        self.input_box.delete("1.0", tk.END)
        self.attached_files.clear()
        self.attach_label.configure(text="")
        self.attach_btn.configure(text="\U0001F4CE Attach Files")
        self._placeholder_active = True
        self.input_box.insert("1.0", "Type your message here...")
        self.input_box.configure(fg=self.theme["placeholder"])

    def _poll_snapshot_state(self):
        def _check():
            try:
                resp = requests.get(f"{self.api_base}/status", timeout=3)
                if resp.status_code == 200:
                    has = resp.json().get("has_snapshots", False)
                    self.root.after(0, lambda: self.rollback_btn.configure(
                        state=tk.NORMAL if has else tk.DISABLED))
            except Exception:
                pass
        threading.Thread(target=_check, daemon=True).start()
        self.root.after(5000, self._poll_snapshot_state)

    # ── Ribbon / Status Updates ──────────────────────────────────

    def _update_ribbon(self):
        # Active window
        try:
            import pygetwindow as gw
            win = gw.getActiveWindow()
            title = (win.title[:30] + "...") if win and len(win.title) > 30 else (win.title if win else "...")
        except Exception:
            title = "..."
        self.ribbon_active.configure(text=f"Active App: {title}")

        # Tool
        tool_text = f"Tool: {self.last_tool}" if self.last_tool else "No active tools"
        self.ribbon_tool.configure(text=tool_text)

        # Status
        self.ribbon_status.configure(text=f"Status: {self.agent_status}")

        self.root.after(2000, self._update_ribbon)

    def _update_status_bar(self):
        try:
            cpu = psutil.cpu_percent(interval=0)
            ram = psutil.virtual_memory().percent
            # Get memory count from service
            mem_count = "?"
            try:
                resp = requests.get(f"{self.api_base}/status", timeout=2)
                if resp.status_code == 200:
                    mem_count = resp.json().get("memory_entries", "?")
            except Exception:
                pass
            self.status_label.configure(
                text=f"Status: {self.agent_status}  \u2022  "
                     f"Model: BitNet-b1.58-2B-4T  \u2022  "
                     f"CPU: {cpu:.0f}%  \u2022  RAM: {ram:.0f}%  \u2022  "
                     f"Memory: {mem_count} conversations"
            )
        except Exception:
            pass
        self.root.after(3000, self._update_status_bar)

    # ── Window Close ─────────────────────────────────────────────

    def _on_close(self):
        try:
            geo = self.root.geometry()
            _save_config_value("gui_geometry", geo)
        except Exception:
            pass
        self.root.destroy()

    def run(self):
        self.root.mainloop()


def main():
    gui = EmberGUI()
    gui.run()


if __name__ == "__main__":
    main()
