"""Password-encrypted, user-controlled custody of the existing profile keys.

Creation and verification use read-only SSH processes. Production key values
never reach stdout, command arguments, logs, or plaintext files. The optional
local viewer reveals values only in the user's own window after decryption.
"""
import argparse
import base64
import hashlib
import hmac
import inspect
import json
import os
import subprocess
import threading
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

KEY_NAMES = ("PROFILE_FIELD_ENCRYPTION_KEY", "PROFILE_FIELD_HMAC_KEY")
FORMAT = "yishu-profile-key-custody-v1"
AAD = b"YSGH/profile-field-key-custody/v1"
KDF = "scrypt-n32768-r8-p1"
MAX_BYTES = 131072


def decode64(value, size=None):
    if not isinstance(value, str) or len(value) > MAX_BYTES:
        raise ValueError("Invalid encrypted backup")
    raw = base64.b64decode(value, validate=True)
    if size is not None and len(raw) != size:
        raise ValueError("Invalid key or encrypted backup")
    return raw


def validate_keys(keys):
    if not isinstance(keys, dict) or set(keys) != set(KEY_NAMES):
        raise ValueError("Two profile keys are required")
    first, second = (decode64(keys[name], 32) for name in KEY_NAMES)
    if hmac.compare_digest(first, second):
        raise ValueError("The two profile keys must differ")


def derive_key(password, salt):
    return Scrypt(salt=salt, length=32, n=32768, r=8, p=1).derive(password.encode("utf-8"))


def seal_keys(keys, password, created_at):
    validate_keys(keys)
    salt, nonce = os.urandom(16), os.urandom(12)
    plaintext = json.dumps({"keys": keys, "created_at": created_at}).encode("utf-8")
    ciphertext = AESGCM(derive_key(password, salt)).encrypt(nonce, plaintext, AAD)
    return {"format": FORMAT, "kdf": KDF,
            "salt": base64.b64encode(salt).decode("ascii"),
            "nonce": base64.b64encode(nonce).decode("ascii"),
            "ciphertext": base64.b64encode(ciphertext).decode("ascii")}


def open_keys(envelope, password):
    if not isinstance(envelope, dict) or set(envelope) != {"format", "kdf", "salt", "nonce", "ciphertext"}:
        raise ValueError("Invalid encrypted backup")
    if envelope["format"] != FORMAT or envelope["kdf"] != KDF:
        raise ValueError("Unsupported encrypted backup")
    salt, nonce = decode64(envelope["salt"], 16), decode64(envelope["nonce"], 12)
    raw = AESGCM(derive_key(password, salt)).decrypt(nonce, decode64(envelope["ciphertext"]), AAD)
    payload = json.loads(raw)
    validate_keys(payload["keys"])
    return payload


REMOTE = '''
import sys
from pathlib import Path
from datetime import datetime, timezone
from dotenv import dotenv_values
sys.dont_write_bytecode = True
names = KEY_NAMES
path = Path('/opt/yishu/deploy/.env')
stored = dotenv_values(path)
def effective(service):
    pid = subprocess.check_output(['systemctl','show',service,'-p','MainPID','--value'],text=True).strip()
    runtime = dict(x.split('=',1) for x in Path('/proc',pid,'environ').read_bytes().decode().split('\\0') if '=' in x)
    return {**dotenv_values('/opt/yishu/backend/.env'), **runtime}
api, worker = effective('yishu-api'), effective('yishu-worker')
keys = {k: stored[k] for k in names}
validate_keys(keys)
assert all(hmac.compare_digest(keys[k], api[k]) and hmac.compare_digest(keys[k], worker[k]) for k in names)
request = json.loads(base64.b64decode(REQUEST))
if request['mode'] == 'create':
    answer = seal_keys(keys, request['password'], datetime.now(timezone.utc).isoformat())
else:
    recovered = open_keys(request['envelope'], request['password'])['keys']
    assert all(hmac.compare_digest(recovered[k], keys[k]) for k in names)
    os.environ.update({k:v for k,v in api.items() if v is not None})
    os.chdir('/opt/yishu/backend')
    sys.path.insert(0,'/opt/yishu/backend')
    from app.services.profile_sensitive_crypto import decrypt_topic, topic_digest
    probe = 'custody-restore-' + os.urandom(16).hex()
    nonce = os.urandom(12)
    encrypted = AESGCM(decode64(recovered[names[0]],32)).encrypt(nonce,probe.encode(),b'profile_sensitive.topic:v1')
    ciphertext = 'v1:' + base64.b64encode(nonce+encrypted).decode()
    digest = hmac.new(decode64(recovered[names[1]],32),probe.encode(),hashlib.sha256).hexdigest()
    assert decrypt_topic(ciphertext) == probe and hmac.compare_digest(topic_digest(probe), digest)
    answer = {'verified_at':datetime.now(timezone.utc).isoformat(),'key_names':list(names),
              'source':'/opt/yishu/deploy/.env','backup_matches_runtime':True,'api_worker_match':True,
              'recovered_aes_and_hmac_roundtrip':True,'production_modified':False,
              'plaintext_key_files_created':False,'production_keys_decrypted_on_local_machine':False}
print(json.dumps(answer))
'''


def remote_call(mode, password, envelope, ssh_key, host):
    if host != "root@8.159.145.112" or not Path(ssh_key).is_file():
        raise ValueError("Expected the configured production host and existing SSH identity")
    common = (
        "import base64,hashlib,hmac,json,os,subprocess\n"
        "from cryptography.hazmat.primitives.ciphers.aead import AESGCM\n"
        "from cryptography.hazmat.primitives.kdf.scrypt import Scrypt\n"
    )
    for name in ("KEY_NAMES", "FORMAT", "AAD", "KDF", "MAX_BYTES"):
        common += name + "=" + repr(globals()[name]) + "\n"
    common += "\n".join(inspect.getsource(f) for f in (decode64, validate_keys, derive_key, seal_keys, open_keys))
    request = base64.b64encode(json.dumps({"mode": mode, "password": password, "envelope": envelope}).encode()).decode()
    program = (common + "\nREQUEST=" + repr(request) + "\n" + REMOTE).encode()
    command = ["C:/Windows/System32/OpenSSH/ssh.exe", "-i", ssh_key, "-o", "BatchMode=yes",
               "-o", "IdentitiesOnly=yes", "-o", "StrictHostKeyChecking=yes", "-o", "ConnectTimeout=10",
               host, "env", "PYTHONDONTWRITEBYTECODE=1", "/opt/yishu/backend/.venv/bin/python", "-"]
    # Fixed host, fixed options and argv execution; no shell or secret arguments.
    result = subprocess.run(command, input=program, capture_output=True, timeout=90, check=False)  # noqa: S603
    if result.returncode or len(result.stdout) > MAX_BYTES:
        # Never persist/print remote stderr: Python tracebacks can include input.
        raise RuntimeError("远端核验未通过，未输出密钥；请保留现有文件并检查连接。")
    return json.loads(result.stdout)


def read_envelope(path):
    if path.stat().st_size > MAX_BYTES:
        raise ValueError("Invalid encrypted backup")
    return json.loads(path.read_text(encoding="utf-8"))


def create_backup(password, output, receipt, ssh_key, host):
    if len(password) < 12:
        raise ValueError("保管密码至少12个字符。")
    if receipt.exists():
        raise ValueError("已有核验收据，请保留现有保管文件。")
    if output.exists():
        encoded = output.read_bytes()
        read_envelope(output)
    else:
        envelope = remote_call("create", password, None, ssh_key, host)
        if envelope.get("format") != FORMAT:
            raise ValueError("Invalid encrypted backup")
        encoded = (json.dumps(envelope, indent=2) + "\n").encode()
        output.parent.mkdir(parents=True, exist_ok=True)
        with os.fdopen(os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as f:
            f.write(encoded)
    verified = remote_call("verify", password, read_envelope(output), ssh_key, host)
    if not verified.get("backup_matches_runtime") or not verified.get("recovered_aes_and_hmac_roundtrip"):
        raise RuntimeError("保管文件恢复核验未完成，请勿替换生产密钥。")
    verified.update({"backup_path": str(output.resolve()), "backup_sha256": hashlib.sha256(encoded).hexdigest(),
                     "custodian": "user", "verified_from_saved_encrypted_file": True})
    receipt.parent.mkdir(parents=True, exist_ok=True)
    receipt.write_text(json.dumps(verified, indent=2) + "\n", encoding="utf-8")


def create_window(args):
    import tkinter as tk
    from tkinter import messagebox, ttk

    root = tk.Tk()
    root.title("忆述光华 · 生产密钥保管")
    root.geometry("600x330")
    root.attributes("-topmost", True)
    ttk.Label(root, text="设置这份保管文件的密码", font=("Microsoft YaHei UI", 14)).pack(pady=(20, 8))
    ttk.Label(root, text=("至少12个字符，输入两次。请保存在你自己的密码管理器中。\n"
                          "密码和密钥值都不会发送到聊天，也不会写入明文文件。"),
              justify="center").pack()
    first, second = tk.StringVar(), tk.StringVar()
    ttk.Label(root, text="保管密码").pack(pady=(14, 0))
    entry = ttk.Entry(root, textvariable=first, show="*", width=48)
    entry.pack()
    ttk.Label(root, text="再次输入").pack(pady=(8, 0))
    confirm = ttk.Entry(root, textvariable=second, show="*", width=48)
    confirm.pack()
    status = tk.StringVar(value="生产钥匙保持原样；这里只生成加密副本。")
    ttk.Label(root, textvariable=status).pack(pady=12)

    def complete(error):
        if error:
            status.set("未完成，请保留已有文件；没有修改生产钥匙。")
            messagebox.showerror("交接未完成", error, parent=root)
            button.configure(state="normal")
            entry.configure(state="normal")
            confirm.configure(state="normal")
            return
        messagebox.showinfo(
            "保管文件已生成并核验",
            "加密副本已保存，且已在服务器内验证能恢复同一套原钥匙。\n\n"
            "请保存保管密码，并将文件另存到你的安全保管位置。",
            parent=root,
        )
        root.destroy()

    def save():
        password = first.get()
        if len(password) < 12 or password != second.get():
            messagebox.showerror("请检查密码", "密码至少12个字符，两次输入须一致。", parent=root)
            return
        first.set("")
        second.set("")
        entry.configure(state="disabled")
        confirm.configure(state="disabled")
        button.configure(state="disabled")
        status.set("正在加密保存并核验恢复，请稍候……")

        def worker():
            error = None
            try:
                create_backup(password, args.output, args.receipt, args.ssh_key, args.host)
            except Exception:
                error = "连接、保存或恢复核验未完成。程序没有输出密钥或密码。"
            root.after(0, complete, error)

        threading.Thread(target=worker, daemon=True).start()

    button = ttk.Button(root, text="加密保存并验证", command=save)
    button.pack()
    entry.focus_set()
    root.mainloop()
    return args.receipt.exists()


def open_window(path):
    import tkinter as tk
    from tkinter import messagebox, simpledialog, ttk

    root = tk.Tk()
    root.withdraw()
    password = simpledialog.askstring(
        "打开密钥保管文件", "请输入你设置的保管密码（不发到聊天）：", show="*", parent=root,
    )
    if password is None:
        root.destroy()
        return False
    try:
        keys = open_keys(read_envelope(path), password)["keys"]
    except Exception:
        messagebox.showerror("无法打开", "密码不正确，或保管文件不完整。", parent=root)
        root.destroy()
        return False
    root.deiconify()
    root.title("忆述光华 · 查看你保管的原密钥")
    root.geometry("680x260")
    ttk.Label(root, text="以下为原生产密钥；请保管到密码管理器，勿粘贴到聊天。", padding=12).pack()
    for name in KEY_NAMES:
        ttk.Label(root, text=name).pack(pady=(10, 0))
        row = ttk.Frame(root)
        row.pack()
        field = ttk.Entry(row, show="*", width=58)
        field.insert(0, keys[name])
        field.configure(state="readonly")
        field.pack(side="left")

        def copy(value=keys[name]):
            root.clipboard_clear()
            root.clipboard_append(value)

        ttk.Button(row, text="复制", command=copy).pack(side="left", padx=6)
    root.mainloop()
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("create", "open"))
    parser.add_argument("--input", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--receipt", type=Path)
    parser.add_argument("--ssh-key")
    parser.add_argument("--host")
    args = parser.parse_args()
    if args.mode == "create":
        if not all((args.output, args.receipt, args.ssh_key, args.host)):
            parser.error("create requires output, receipt, ssh-key and host")
        ok = create_window(args)
    else:
        if args.input is None:
            parser.error("open requires input")
        ok = open_window(args.input)
    print(json.dumps({"completed": ok, "mode": args.mode}))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
