"""Shared navigation, appearance and worker for desktop demos."""

from concurrent.futures import ThreadPoolExecutor
import tkinter as tk
from tkinter import font, ttk


BACKGROUND = "#141a21"
PANEL = "#202a33"
DRAWING = "#10161c"
BORDER = "#33424e"
TEXT = "#eef4f6"
MUTED = "#a9bcc9"
ACCENT = "#90ecd2"
ERROR = "#ffb8af"


class DemoWindow:
    def __init__(self, root, demos, args, load_module):
        self.root = root
        self.demos = demos
        self.args = args
        self.load_module = load_module
        self.worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="npu-demo")
        self.page = None
        self.root.title("ChipDesign Demo")
        self.root.configure(bg=BACKGROUND)
        self.root.minsize(780, 590)
        families = set(font.families(root))
        self.family = next(
            (name for name in ("Microsoft YaHei UI", "Noto Sans CJK SC", "PingFang SC")
             if name in families),
            font.nametofont("TkDefaultFont").actual("family"),
        )
        style = ttk.Style(root)
        style.theme_use("clam")
        style.configure("Demo.TButton", background=PANEL, foreground=TEXT, bordercolor=BORDER,
                        lightcolor=PANEL, darkcolor=PANEL, focuscolor=ACCENT,
                        font=(self.family, 11), padding=(13, 7))
        style.map("Demo.TButton", background=[("active", "#32434e")],
                  foreground=[("disabled", MUTED)])
        style.configure("DemoPrimary.TButton", background=ACCENT, foreground=DRAWING,
                        lightcolor=ACCENT, darkcolor=ACCENT, bordercolor=ACCENT,
                        focuscolor=DRAWING, font=(self.family, 11, "bold"), padding=(15, 8))
        style.map("DemoPrimary.TButton", background=[("active", "#b1f5e3")])

        header = tk.Frame(root, bg=BACKGROUND, padx=28, pady=15)
        header.pack(fill="x")
        self.label(header, "ChipDesign", 14, bold=True).pack(side="left")
        self.label(header, "Demo", 11, fg=MUTED).pack(side="left", padx=(12, 0))
        self.back = ttk.Button(header, text="全部 Demo", style="Demo.TButton",
                               command=self.show_picker)
        self.back.pack(side="right")
        tk.Frame(root, height=1, bg=BORDER).pack(fill="x")
        viewport = tk.Frame(root, bg=BACKGROUND)
        viewport.pack(fill="both", expand=True)
        self.scroller = tk.Canvas(viewport, bg=BACKGROUND, highlightthickness=0)
        scrollbar = ttk.Scrollbar(viewport, orient="vertical", command=self.scroller.yview)
        scrollbar.pack(side="right", fill="y")
        self.scroller.pack(side="left", fill="both", expand=True)
        self.scroller.configure(yscrollcommand=scrollbar.set)
        self.content = tk.Frame(self.scroller, bg=BACKGROUND, padx=28, pady=20)
        self.content_window = self.scroller.create_window((0, 0), window=self.content, anchor="nw")
        self.content.bind("<Configure>", lambda _: self.scroller.configure(scrollregion=self.scroller.bbox("all")))
        self.scroller.bind("<Configure>", lambda event: self.scroller.itemconfigure(self.content_window, width=event.width))
        root.bind_all("<MouseWheel>", lambda event: self.scroller.yview_scroll(-int(event.delta / 120), "units"))
        root.bind_all("<Button-4>", lambda _: self.scroller.yview_scroll(-1, "units"))
        root.bind_all("<Button-5>", lambda _: self.scroller.yview_scroll(1, "units"))
        root.geometry(f"{min(980,root.winfo_screenwidth()-60)}x{min(740,root.winfo_screenheight()-80)}")
        root.protocol("WM_DELETE_WINDOW", self.close)
        self.show_picker()

    def label(self, parent, text, size=11, *, fg=TEXT, bold=False, **options):
        return tk.Label(parent, text=text, fg=fg, bg=parent.cget("bg"),
                        font=(self.family, size, "bold" if bold else "normal"), **options)

    def replace_page(self, page):
        if self.page is not None:
            self.page.destroy()
        self.page = page
        self.page.pack(fill="both", expand=True)
        self.root.update_idletasks()
        self.scroller.yview_moveto(0)

    def show_picker(self):
        self.back.state(["disabled"])
        page = tk.Frame(self.content, bg=BACKGROUND)
        self.replace_page(page)
        self.label(page, "选择 Demo", 25, bold=True).pack(anchor="w", pady=(8, 20))
        grid = tk.Frame(page, bg=BACKGROUND)
        grid.pack(fill="both", expand=True)
        for col in (0, 1):
            grid.columnconfigure(col, weight=1, uniform="cards")
        for index, (key, (title, description, _)) in enumerate(self.demos.items()):
            row = tk.Frame(grid, bg=PANEL, padx=16, pady=15,
                           highlightbackground=BORDER, highlightthickness=1)
            row.grid(row=index // 2, column=index % 2, sticky="nsew", padx=(0, 8), pady=(0, 12))
            self.label(row, title, 14, bold=True).pack(anchor="w")
            self.label(row, description, fg=MUTED, wraplength=310, justify="left").pack(anchor="w", pady=(4, 14))
            ttk.Button(row, text="打开", style="DemoPrimary.TButton",
                       command=lambda name=key: self.open_demo(name)).pack(anchor="e")

    def open_demo(self, name):
        title, _, module = self.demos[name]
        try:
            page = self.load_module(module).Demo(self.content, self)
        except (ImportError, OSError, ValueError, KeyError) as exc:
            page = tk.Frame(self.content, bg=BACKGROUND)
            self.label(page, title, 25, bold=True).pack(anchor="w", pady=(8, 12))
            self.label(page, f"无法启动 Demo\n{exc}", fg=ERROR, justify="left",
                       wraplength=660).pack(anchor="w")
        self.replace_page(page)
        self.back.state(["!disabled"])

    def close(self):
        if self.page is not None:
            self.page.destroy()
        self.worker.shutdown(wait=False)
        self.root.destroy()
