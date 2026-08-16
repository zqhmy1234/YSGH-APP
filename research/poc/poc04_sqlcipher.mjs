// POC-04 · SQLCipher 桌面验证（Node better-sqlite3 SQLCipher 版）
// 验证加密 SQLite：写入 → 明文工具读不出 → 正确密钥可读 → 错误密钥拒绝
// 运行：node research/poc/poc04_sqlcipher.mjs
import Database from "better-sqlite3-multiple-ciphers";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { execFileSync } from "node:child_process";

const dir = mkdtempSync(join(tmpdir(), "yishu_poc04_"));
const dbPath = join(dir, "encrypted.db");
const KEY = "test-key-2026";

// 1. 加密写入
const db = new Database(dbPath);
db.pragma(`key = '${KEY}'`);
db.exec("CREATE TABLE mem (id INTEGER PRIMARY KEY, text TEXT)");
db.prepare("INSERT INTO mem (text) VALUES (?)").run("敏感记忆内容");
db.close();

// 2. 用系统 sqlite3 CLI（明文）尝试读取 → 应失败
let plainFailed = false;
try {
  execFileSync("sqlite3", [dbPath, "SELECT count(*) FROM mem"], { stdio: "pipe", timeout: 5000 });
} catch {
  plainFailed = true;
}

// 3. 正确密钥重开
const db2 = new Database(dbPath);
db2.pragma(`key = '${KEY}'`);
const row = db2.prepare("SELECT text FROM mem").get();
db2.close();

// 4. 错误密钥 → 应报错
let wrongKeyFailed = false;
try {
  const db3 = new Database(dbPath);
  db3.pragma("key = 'wrong-key'");
  db3.prepare("SELECT count(*) FROM mem").get();
  db3.close();
} catch {
  wrongKeyFailed = true;
}

const ok = plainFailed && row?.text === "敏感记忆内容" && wrongKeyFailed;
console.log("=".repeat(50));
console.log("POC-04 SQLCipher 桌面验证（Node better-sqlite3）");
console.log("=".repeat(50));
console.log(`  写入/读取: ${row?.text === "敏感记忆内容" ? "PASS" : "FAIL"} (${row?.text})`);
console.log(`  明文 sqlite3 无法读取: ${plainFailed ? "PASS" : "FAIL"}`);
console.log(`  错误密钥拒绝: ${wrongKeyFailed ? "PASS" : "FAIL"}`);
console.log(`  结果: ${ok ? "PASS 通过" : "FAIL 未通过"}`);
process.exit(ok ? 0 : 1);
