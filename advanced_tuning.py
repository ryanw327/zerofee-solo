"""Modal editor; changes are saved atomically for the next user-started session."""
import tkinter as tk
from tkinter import ttk
from advanced_settings import BATCH_SIZES, THREAD_COUNTS, PACING, TuningSettings

class AdvancedTuning:
    def __init__(self, app):
        self.app = app
        self.window = window = tk.Toplevel(app.root)
        window.title('Advanced GPU Tuning')
        window.transient(app.root)
        window.resizable(False, False)
        frame = ttk.Frame(window, padding=20); frame.pack(fill='both', expand=True)
        ttk.Label(frame, text='Advanced GPU Tuning', font=('Segoe UI', 16, 'bold')).grid(row=0, column=0, columnspan=2, sticky='w')
        ttk.Label(frame, text='Saved settings apply to your next session. Stop stays available during a benchmark.',
                  wraplength=520).grid(row=1, column=0, columnspan=2, sticky='w', pady=(6,14))
        ttk.Label(frame, text='USE AT YOUR OWN RISK: sustained GPU stress and electricity costs.\n'
            'Cutoffs cannot guarantee safety. Software is as-is, without warranty; solo odds are extremely low.',
            font=('Segoe UI',9,'bold'),wraplength=540).grid(row=2,column=0,columnspan=2,sticky='w',pady=(0,12))
        self.variables = {name: tk.StringVar(window) for name in TuningSettings.__dataclass_fields__}
        self.widgets = []
        rows = [('Pacing', 'pacing', list(PACING)), ('Custom pause (ms)', 'custom_pause_ms', None),
                ('Hashes per batch', 'batch_size', BATCH_SIZES), ('Threads per block', 'threads_per_block', THREAD_COUNTS),
                ('GPU core stop (°C)', 'temperature_limit_c', None)]
        for row, (label, name, values) in enumerate(rows, 3):
            ttk.Label(frame, text=label).grid(row=row, column=0, sticky='w', pady=5)
            widget = (ttk.Combobox(frame, textvariable=self.variables[name], values=values, state='readonly', width=29)
                      if values else ttk.Entry(frame, textvariable=self.variables[name], width=32))
            widget.grid(row=row, column=1, sticky='ew', padx=(16,0), pady=5)
            self.widgets.append((name, widget, bool(values)))
        self.variables['pacing'].trace_add('write', lambda *_: self.refresh())
        ttk.Label(frame, text='Use at your own risk. Sustained GPU stress can cause heat, instability and hardware wear; the cutoff cannot guarantee safety.\n'
            'Pause: 0–5000 ms. Core stop: 60–90°C (default 80°C). Missing core readings stop after 15 seconds.\n'
            'Larger batches may delay Stop until the current kernel finishes. Pacing is idle time, not a power cap.\n'
            'Hotspot is not monitored. Not all presets push maximum load. No overclock, voltage or fan changes.', wraplength=540).grid(row=8, column=0, columnspan=2, sticky='w', pady=(10,14))
        bench = ttk.LabelFrame(frame, text='60-second synthetic benchmark', padding=10)
        bench.grid(row=9, column=0, columnspan=2, sticky='ew')
        ttk.Label(bench, text='Compare three batch/thread pairs using the pause and core cutoff above.\n'
            'No Core connection, block submission or rewards. A short result needs sustained thermal checking.', wraplength=520).pack(anchor='w')
        actions = ttk.Frame(bench); actions.pack(fill='x', pady=(8,0))
        self.benchmark_button = ttk.Button(actions, text='Start 60-second sweep', command=self.benchmark)
        self.benchmark_button.pack(side='left')
        self.stop_button = ttk.Button(actions, text='Cancel benchmark', command=app.stop)
        self.stop_button.pack(side='left', padx=8)
        self.benchmark_text = tk.StringVar(window, value='Idle. Other Python/miner processes must be closed first.')
        ttk.Label(bench, textvariable=self.benchmark_text, wraplength=520).pack(anchor='w', pady=(8,0))
        self.message = tk.StringVar(window)
        ttk.Label(frame, textvariable=self.message, wraplength=540, foreground='#a02020').grid(row=10, column=0, columnspan=2, sticky='w', pady=8)
        buttons = ttk.Frame(frame); buttons.grid(row=11, column=0, columnspan=2, sticky='ew')
        self.reset_button = ttk.Button(buttons, text='Reset defaults', command=self.reset); self.reset_button.pack(side='left')
        self.cancel_button = ttk.Button(buttons, text='Cancel', command=self.close); self.cancel_button.pack(side='right')
        self.save_button = ttk.Button(buttons, text='Save for next session', command=self.save); self.save_button.pack(side='right', padx=8)
        self.populate(app.tuning)
        window.protocol('WM_DELETE_WINDOW', self.close)
        window.bind('<Escape>', lambda _: self.close())
        window.grab_set()
        self.refresh()

    def populate(self, settings):
        for name, variable in self.variables.items():
            variable.set(str(getattr(settings, name)))

    def chosen(self):
        value = {name: variable.get() for name, variable in self.variables.items()}
        try:
            value['custom_pause_ms'] = float(value['custom_pause_ms'])
            value['batch_size'] = int(value['batch_size'])
            value['threads_per_block'] = int(value['threads_per_block'])
            value['temperature_limit_c'] = float(value['temperature_limit_c'])
        except (ValueError, OverflowError):
            raise ValueError('Enter numbers for pause, batch size, threads and GPU core stop.')
        return TuningSettings(**value).validate()

    def locked(self):
        return self.app.runner.busy or self.app.awaiting_result or self.app.detecting or self.app.quitting

    def refresh(self):
        if not hasattr(self, 'save_button'):
            return
        locked = self.locked()
        for name, widget, choice in self.widgets:
            disabled = locked or name == 'custom_pause_ms' and self.variables['pacing'].get() != 'Custom pause'
            widget.configure(state='disabled' if disabled else 'readonly' if choice else 'normal')
        for widget in (self.save_button, self.reset_button, self.benchmark_button):
            widget.configure(state='disabled' if locked else 'normal')
        self.stop_button.configure(state='normal' if self.app.runner.busy and not self.app.runner.stop_event.is_set() else 'disabled')
        self.cancel_button.configure(text='Cancel benchmark & close' if locked else 'Cancel')

    def save(self):
        if self.locked():
            return
        try:
            self.app.save_tuning(self.chosen())
        except (ValueError, OSError) as exc:
            self.message.set(str(exc)); return
        self.close()

    def reset(self):
        if not self.locked():
            self.populate(TuningSettings())
            self.message.set('Defaults restored in this draft. Save to keep them; Cancel discards them.')

    def benchmark(self):
        if self.locked():
            return
        try:
            self.app.runner.start_benchmark(self.chosen())
            self.app.awaiting_result = True
            self.app.status.set('Synthetic GPU benchmark')
            self.benchmark_text.set('Starting; 60 seconds includes setup. Cancel stops after the current GPU call.')
        except (ValueError, OSError, RuntimeError) as exc:
            self.message.set(str(exc))
        self.app.refresh_controls()

    def progress(self, value):
        self.benchmark_text.set(f"Pair {value['variant']}/{value['total_variants']}: {value['batch_size']:,} hashes, {value['threads_per_block']} threads · {value['elapsed_seconds']:.0f}/60 seconds")

    def finished(self, record):
        if record.get('error'):
            self.benchmark_text.set(record['error']); return
        result = record.get('benchmark') or {}
        lines = [f"{row['batch_size']:,} / {row['threads_per_block']} threads: {row['mh_s']:.2f} MH/s"
                 + (' (partial)' if not row['complete'] else '') for row in result.get('rows', [])]
        self.benchmark_text.set(result.get('status', 'Stopped') + ' — synthetic GPU benchmark\n' + '\n'.join(lines)
            + ('\n' + result['stop_reason'] if result.get('stop_reason') else '')
            + ('\nUnsupported thread sizes skipped.' if result.get('skipped') else '') + '\nSettings were not changed.')
        best=result.get('best_starting_point')
        if best:
            self.benchmark_text.set(self.benchmark_text.get()+f"\nFastest starting point: {best['batch_size']:,} / {best['threads_per_block']} threads. Short samples do not measure thermal soak.")

    def close(self):
        if self.app.runner.busy or self.app.awaiting_result:
            self.app.stop()
            self.message.set('Cancelling. Wait for the worker to release the GPU, then close this dialog.')
            return
        self.window.grab_release(); self.window.destroy()
        self.app.advanced_dialog = None
        self.app.refresh_controls()
