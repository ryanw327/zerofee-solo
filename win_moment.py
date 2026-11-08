"""Accepted-block receipts and a lightweight celebration; no node/GPU access."""
from datetime import datetime,timezone
from pathlib import Path
import json,re
import tkinter as tk
from tkinter import ttk
from address import payout_script
from tip_support import TipSection,tip_eligible

def receipt_snapshots(record):
    """Validate known accepted outcomes without touching storage or a journal."""
    result=record.get('result') or {}
    if record.get('mode') in ('simulation','recovery') or result.get('submission_uncertain'):return []
    receipts=[]
    for block in result.get('blocks') or []:
        if block.get('accepted') is not True:continue
        network=block.get('network') or result.get('chain')
        address=block.get('payout_address') or record.get('payout_address')
        height=block.get('height');digest=block.get('hash')
        if network not in ('mainnet','regtest') or not isinstance(digest,str) or not re.fullmatch('[0-9a-f]{64}',digest):
            raise ValueError('Accepted-block receipt has invalid network or hash')
        if type(height) is not int or height<0:raise ValueError('Accepted-block receipt has invalid height')
        payout_script(address,network)
        confirmations=block.get('node_confirmations')
        if type(confirmations) is not int:confirmations=None
        receipt=dict(recorded_utc=record.get('recorded_utc') or datetime.now(timezone.utc).isoformat(),
            network=network,block_hash=digest,height=height,payout_address=address,
            accepted_by_node=True,active_chain_at_check=block.get('active_chain') is True,
            node_confirmations_at_check=confirmations,
            confirmation_note='Snapshot from this node. Acceptance does not establish network propagation or future chain permanence.')
        receipts.append(receipt)
    return receipts

def save_receipts(record,root):
    """Persist accepted snapshots atomically; preserve the first receipt."""
    receipts=[]
    for receipt in receipt_snapshots(record):
        network,digest=receipt['network'],receipt['block_hash']
        folder=Path(root)/'wins';folder.mkdir(parents=True,exist_ok=True)
        path=folder/(network+'-'+digest+'.json')
        if path.exists():
            existing=json.loads(path.read_text(encoding='utf-8'))
            if any(existing.get(key)!=receipt[key] for key in ('network','block_hash','height','payout_address')):
                raise ValueError('Existing win receipt conflicts with the accepted block')
            receipt=existing
        else:
            temporary=path.with_suffix('.tmp')
            temporary.write_text(json.dumps(receipt,indent=2)+'\n',encoding='utf-8');temporary.replace(path)
        receipts.append(dict(**receipt,receipt_path=str(path)))
    return receipts

def continuation_allowed(record):
    result=record.get('result') or {}
    return bool(record.get('mode')=='mainnet' and not record.get('error')
        and not record.get('restoration_error') and not record.get('stop_reason') and not record.get('win_receipt_error') and not result.get('submission_uncertain')
        and (record.get('restoration') or {}).get('temporary_access_removed') is True)

def chain_text(receipt,historical=False):
    active='In your node’s active chain' if receipt.get('active_chain_at_check') else 'Not in your node’s active chain at the check'
    count=receipt.get('node_confirmations_at_check')
    if historical:
        active=('Was in your node’s active chain at the saved check' if receipt.get('active_chain_at_check') else
                'Was not in your node’s active chain at the saved check')
        return active+'\nConfirmations at that check: '+('not available' if count is None else str(count))+'\nCurrent chain status and confirmations: not checked'
    return active+'\nNode-reported confirmations: '+('not available' if count is None else str(count))

class WinMoment:
    def __init__(self,parent,receipt,on_continue=None,allow_continue=False,notice='',sound=True,visible=True,historical=False):
        if historical:
            allow_continue=False;on_continue=None;sound=False
            notice='Saved receipt only. Viewing it does not verify current Core state or restart mining.'
        self.historical=historical
        self.closed=False;self.on_continue=on_continue
        self.window=win=tk.Toplevel(parent)
        if not visible:win.withdraw()
        test=receipt['network']!='mainnet'
        win.title('REGTEST accepted block — no Bitcoin won' if test else 'ZeroFee Solo — block accepted!')
        if historical:win.title('REGTEST saved receipt - no Bitcoin won' if test else 'ZeroFee Solo - saved block receipt')
        win.geometry('760x640');win.minsize(720,500);win.transient(parent)
        win.configure(background='#073b2a')
        heading=('REGTEST SAVED RECEIPT' if test else 'SAVED BLOCK RECEIPT') if historical else ('REGTEST - ACCEPTED BLOCK' if test else '★  BLOCK ACCEPTED!  ★')
        tk.Label(win,text=heading,
            bg='#073b2a',fg='#ffe082',font=('Segoe UI',25,'bold')).pack(pady=(24,8))
        tk.Label(win,text='Saved record of your node’s acceptance.' if historical else 'Your node accepted your block.',bg='#073b2a',fg='white',
            font=('Segoe UI',15,'bold')).pack()
        body=ttk.Frame(win,padding=20);body.pack(fill='both',expand=True,padx=20,pady=16)
        # Keep Finish/Continue visible even when the optional section is expanded.
        row=ttk.Frame(body);row.pack(side='bottom',fill='x',pady=(12,0))
        viewport=ttk.Frame(body);viewport.pack(fill='both',expand=True)
        self.scroll_canvas=tk.Canvas(viewport,highlightthickness=0,borderwidth=0)
        scrollbar=ttk.Scrollbar(viewport,orient='vertical',command=self.scroll_canvas.yview)
        scrollbar.pack(side='right',fill='y')
        self.scroll_canvas.pack(side='left',fill='both',expand=True)
        self.scroll_canvas.configure(yscrollcommand=scrollbar.set)
        frame=ttk.Frame(self.scroll_canvas)
        self.content_window=self.scroll_canvas.create_window((0,0),window=frame,anchor='nw')
        frame.bind('<Configure>',lambda event:self.scroll_canvas.configure(scrollregion=self.scroll_canvas.bbox('all')))
        self.scroll_canvas.bind('<Configure>',self.resize_content)
        win.bind('<MouseWheel>',self.scroll_content)
        ttk.Label(frame,text=f"Block height {receipt['height']:,}",font=('Segoe UI',15,'bold')).pack(anchor='w')
        ttk.Label(frame,text=chain_text(receipt,historical),wraplength=630).pack(anchor='w',pady=(5,12))
        if historical:ttk.Label(frame,text='Recorded: '+receipt.get('recorded_utc','not available'),wraplength=630).pack(anchor='w',pady=(0,8))
        self.details=tk.Text(frame,height=5,wrap='word',font=('Consolas',10),borderwidth=0)
        self.details.pack(fill='x')
        self.details.insert('1.0',f"Block hash\n{receipt['block_hash']}\nPayout address\n{receipt['payout_address']}")
        self.details.configure(state='disabled')
        ttk.Label(frame,text='Node acceptance and this confirmation snapshot do not guarantee network propagation or future chain permanence.',wraplength=630).pack(anchor='w',pady=8)
        ttk.Label(frame,text=notice or ('Receipt saved. Normal Core has been restored. Continue opens the usual per-run approval.' if allow_continue else
            'Receipt saved. Review Core/recovery before another run.'),wraplength=630).pack(anchor='w')
        self.maturity_note=ttk.Label(frame,text='Coinbase rewards are subject to Bitcoin\'s 100-block maturity rule. This saved snapshot does not establish spendability.',wraplength=630)
        self.maturity_note.pack(anchor='w',pady=(8,0))
        self.tip_section=None
        if tip_eligible(receipt,allow_continue,notice,historical=historical):
            ttk.Separator(frame,orient='horizontal').pack(fill='x',pady=(12,8))
            self.tip_section=TipSection(frame,historical=historical)
            self.tip_section.pack(fill='x')
        self.continue_button=ttk.Button(row,text='Continue mining…',command=self.continue_mining,
            state='normal' if allow_continue else 'disabled')
        if not historical:self.continue_button.pack(side='right',padx=(10,0))
        self.finish_button=ttk.Button(row,text='Close receipt' if historical else 'Finish for now',command=self.close)
        self.finish_button.pack(side='right')
        win.protocol('WM_DELETE_WINDOW',self.close)
        if sound and not test:
            try:
                import winsound
                winsound.MessageBeep(winsound.MB_ICONASTERISK)
            except (ImportError,OSError):pass
    def resize_content(self,event):
        self.scroll_canvas.itemconfigure(self.content_window,width=max(1,event.width))
        def resize(widget):
            for child in widget.winfo_children():
                if isinstance(child,ttk.Label) and int(child.cget('wraplength') or 0)>0:
                    child.configure(wraplength=max(80,event.width-24))
                resize(child)
        resize(self.scroll_canvas)
    def scroll_content(self,event):
        if event.delta:
            self.scroll_canvas.yview_scroll(-1 if event.delta>0 else 1,'units')
            return 'break'
    def continue_mining(self):
        if self.closed or str(self.continue_button['state'])=='disabled':return
        callback=self.on_continue;self.close()
        if callback:callback()
    def close(self):
        if not self.closed:self.closed=True;self.window.destroy()
