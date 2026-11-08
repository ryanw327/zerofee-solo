"""Read-only saved win browser. No RPC, mining, or live confirmation lookup."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import tkinter as tk
from tkinter import ttk
from address import payout_script
from win_moment import WinMoment

NAME = re.compile(r'(mainnet|regtest)-([0-9a-f]{64})\.json')
MAX_RECEIPT_BYTES = 16_384


def validate_saved_receipt(value, filename):
    """Validate stored structure without treating the file as fresh chain proof."""
    match = NAME.fullmatch(filename)
    if match is None or not isinstance(value, dict):
        raise ValueError('Unrecognized receipt format')
    network, digest = match.groups()
    if value.get('network') != network or value.get('block_hash') != digest:
        raise ValueError('Receipt filename and block identity disagree')
    height = value.get('height')
    if type(height) is not int or height < 0:
        raise ValueError('Invalid block height')
    payout_script(value.get('payout_address'), network)
    if value.get('accepted_by_node') is not True:
        raise ValueError('Receipt does not record node acceptance')
    active = value.get('active_chain_at_check')
    count = value.get('node_confirmations_at_check')
    if type(active) is not bool or (count is not None and type(count) is not int):
        raise ValueError('Invalid saved confirmation snapshot')
    if count is not None and (count < -1 or (active and count < 1) or (not active and count > 0)):
        raise ValueError('Inconsistent saved confirmation snapshot')
    recorded = value.get('recorded_utc')
    if not isinstance(recorded, str):
        raise ValueError('Missing recorded timestamp')
    stamp = datetime.fromisoformat(recorded)
    if stamp.tzinfo is None or stamp.utcoffset() is None:
        raise ValueError('Recorded timestamp has no timezone')
    return {key: value[key] for key in ('recorded_utc','network','block_hash','height',
            'payout_address','accepted_by_node','active_chain_at_check','node_confirmations_at_check')}


def load_saved_wins(root):
    """Read only regular receipt files, preserve originals, return newest first."""
    folder = Path(root) / 'wins'
    receipts, problems = [], []
    try:
        if not folder.exists():
            return receipts, problems
        if folder.is_symlink() or folder.is_junction() or not folder.is_dir():
            return [], ['The saved receipt folder is not a regular local directory.']
        with os.scandir(folder) as entries:
            for entry in entries:
                if not NAME.fullmatch(entry.name):
                    continue
                try:
                    path = Path(entry.path)
                    if not entry.is_file(follow_symlinks=False) or path.is_junction():
                        raise ValueError('Not a regular receipt file')
                    with path.open('rb') as stream:
                        raw = stream.read(MAX_RECEIPT_BYTES + 1)
                    if len(raw) > MAX_RECEIPT_BYTES:
                        raise ValueError('Receipt is too large')
                    receipt = validate_saved_receipt(json.loads(raw.decode('utf-8')), entry.name)
                    receipt['receipt_path'] = str(path)
                    receipts.append(receipt)
                except (OSError, ValueError, TypeError, UnicodeError) as exc:
                    problems.append(entry.name + ': ' + str(exc))
    except OSError as exc:
        problems.append('Could not read saved receipts: ' + str(exc))
    receipts.sort(key=lambda item: (datetime.fromisoformat(item['recorded_utc']),
                                   item['network'], item['block_hash']), reverse=True)
    return receipts, problems


class SavedWins:
    """One list, any number of retained receipts. Opening never starts a worker."""
    def __init__(self, parent, root, visible=True):
        self.parent, self.root = parent, Path(root)
        self.closed = False
        self.receipts = {}
        self.opened = {}
        self.load_errors = []
        self.window = win = tk.Toplevel(parent)
        if not visible:
            win.withdraw()
        win.title('ZeroFee Solo - saved wins')
        win.geometry('780x420'); win.minsize(680,320); win.transient(parent)
        frame = ttk.Frame(win, padding=16); frame.pack(fill='both',expand=True)
        ttk.Label(frame,text='Saved block receipts',font=('Segoe UI',17,'bold')).pack(anchor='w')
        ttk.Label(frame,text='Stored snapshots only. Current chain status, confirmations and spendability are not checked.',wraplength=710).pack(anchor='w',pady=(5,10))
        actions=ttk.Frame(frame);actions.pack(side='bottom',fill='x',pady=(10,0))
        ttk.Button(actions,text='Refresh list',command=self.refresh).pack(side='left')
        self.open_button=ttk.Button(actions,text='Open selected win',command=self.open_selected,state='disabled')
        self.open_button.pack(side='left',padx=8)
        ttk.Button(actions,text='Close',command=self.close).pack(side='right')
        self.status=tk.StringVar()
        ttk.Label(frame,textvariable=self.status,wraplength=710).pack(side='bottom',anchor='w',pady=(8,0))
        table=ttk.Frame(frame);table.pack(fill='both',expand=True)
        columns=('network','height','recorded','confirmations','hash')
        self.tree=ttk.Treeview(table,columns=columns,show='headings',selectmode='browse',height=7)
        for key,title,width in [('network','Network',135),('height','Height',70),('recorded','Recorded UTC',165),
                                ('confirmations','Confirmations at check',145),('hash','Block hash',120)]:
            self.tree.heading(key,text=title);self.tree.column(key,width=width,minwidth=65,stretch=True)
        vertical=ttk.Scrollbar(table,orient='vertical',command=self.tree.yview)
        horizontal=ttk.Scrollbar(table,orient='horizontal',command=self.tree.xview)
        self.tree.configure(yscrollcommand=vertical.set,xscrollcommand=horizontal.set)
        horizontal.pack(side='bottom',fill='x');vertical.pack(side='right',fill='y')
        self.tree.pack(side='left',fill='both',expand=True)
        self.tree.bind('<<TreeviewSelect>>',self.selection_changed)
        self.tree.bind('<Double-1>',self.open_selected)
        self.tree.bind('<Return>',self.open_selected)
        win.protocol('WM_DELETE_WINDOW',self.close)
        self.refresh()

    def selection_changed(self,event=None):
        selected=self.tree.selection()
        self.open_button.configure(state='normal' if selected and selected[0] in self.receipts else 'disabled')

    def refresh(self):
        if self.closed:
            return
        selected=self.tree.selection()
        previous=selected[0] if selected else None
        receipts,self.load_errors=load_saved_wins(self.root)
        for item in self.tree.get_children():
            self.tree.delete(item)
        self.receipts={}
        for receipt in receipts:
            key=receipt['network']+'-'+receipt['block_hash']
            self.receipts[key]=receipt
            stamp=datetime.fromisoformat(receipt['recorded_utc']).astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
            count=receipt['node_confirmations_at_check']
            network='MAINNET' if receipt['network']=='mainnet' else 'REGTEST (no BTC)'
            self.tree.insert('', 'end',iid=key,values=(network,receipt['height'],stamp,
                'Unknown' if count is None else count,receipt['block_hash'][:12]+'...'))
        if previous in self.receipts:
            self.tree.selection_set(previous)
        elif receipts:
            self.tree.selection_set(next(iter(self.receipts)))
        self.selection_changed()
        text=(f'{len(receipts)} saved receipt(s). Open one to view the original snapshot.' if receipts else
              'No saved win receipts yet. Only accepted blocks with a saved receipt appear here.')
        if self.load_errors:
            text+=f' {len(self.load_errors)} read/validation issue(s); files were not changed. '+self.load_errors[0]
        self.status.set(text)

    def open_selected(self,event=None):
        if self.closed:
            return 'break' if event else None
        selection=self.tree.selection()
        if not selection or selection[0] not in self.receipts:
            return 'break' if event else None
        key=selection[0]
        dialog=self.opened.get(key)
        if dialog is None or dialog.closed:
            dialog=WinMoment(self.parent,self.receipts[key],historical=True,sound=False)
            self.opened[key]=dialog
        else:
            dialog.window.deiconify();dialog.window.lift()
        return 'break' if event else dialog

    def show(self):
        if not self.closed:
            self.window.deiconify();self.window.lift()

    def close(self):
        if not self.closed:
            self.closed=True;self.window.destroy()
