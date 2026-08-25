#!/usr/bin/env python3
"""早报 JSON → 单文件 HTML 渲染器（无外部依赖，内联 CSS，自动适配深色模式）。

输入 stdin JSON schema：
{
  "date":  "2026-08-25",
  "major": [{"title":"标题(可含链接文字)", "fact":"一句话事实",
             "why":"为什么重要", "url":"原文链接"}],
  "opps":  [{"what":"做什么", "entry":"门槛", "risk":"风险"}],
  "note":  "可选补充说明"
}
market 数据由 --market 参数传入的 JSON 文件提供 {"btc","eth","fng"}。

用法：python3 scripts/render_digest.py --market /tmp/m.json > out/xxx.html
"""
import argparse
import html
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

SH_TZ = timezone(timedelta(hours=8))  # 与其他脚本统一东八区，避免依赖服务器本地时区

CSS = """
:root{--bg:#f6f7f9;--card:#fff;--tx:#1a1f26;--mut:#5b6472;--acc:#2563eb;
      --opp:#16a34a;--line:#e5e8ec;--warn:#d97706}
@media(prefers-color-scheme:dark){:root{--bg:#12161c;--card:#1b2129;--tx:#e6e9ee;
      --mut:#98a1ad;--acc:#60a5fa;--opp:#4ade80;--line:#2a323d}}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--tx);
     font:15px/1.75 -apple-system,"PingFang SC","Microsoft YaHei",system-ui,sans-serif;
     padding:28px 14px}
main{max-width:680px;margin:0 auto}
header h1{font-size:24px;font-weight:700;letter-spacing:.5px}
header .sub{color:var(--mut);font-size:13px;margin-top:2px}
.market{display:flex;gap:10px;flex-wrap:wrap;margin:18px 0 6px}
.chip{background:var(--card);border:1px solid var(--line);border-radius:10px;
      padding:8px 14px;font-size:13px}
.chip b{display:block;color:var(--mut);font-weight:500;font-size:11px;
        letter-spacing:.5px;margin-bottom:2px}
section{margin-top:26px}
h2{font-size:15px;font-weight:600;color:var(--acc);margin-bottom:12px;
   letter-spacing:1px}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;
      padding:16px 18px;margin-bottom:12px}
.card .no{color:var(--acc);font-weight:700;font-size:13px;margin-right:8px}
.card .t{font-size:16px;font-weight:600}
.card .t a{color:inherit;text-decoration:none;border-bottom:1px solid var(--line)}
.fact{margin-top:6px}
.why{margin-top:8px;padding-left:10px;border-left:3px solid var(--acc);
     color:var(--mut);font-size:13.5px}
.opp{border-left:4px solid var(--opp)}
.opp .row{display:flex;gap:8px;margin-top:4px;font-size:14px}
.opp .k{flex:none;color:var(--opp);font-weight:600;width:44px}
.none{color:var(--mut);font-size:14px;background:var(--card);
      border:1px dashed var(--line);border-radius:12px;padding:14px 18px}
.note{color:var(--warn);font-size:13.5px;background:var(--card);
      border:1px solid var(--line);border-radius:12px;padding:12px 18px;margin-top:20px}
footer{margin-top:30px;padding-top:14px;border-top:1px solid var(--line);
       color:var(--mut);font-size:12px;line-height:1.9}
"""


def esc(s) -> str:
    return html.escape(str(s or ""))


def chip(label, val, fallback="数据缺失"):
    v = esc(val) if val else f'<span style="color:var(--mut)">{fallback}</span>'
    return f'<div class="chip"><b>{label}</b>{v}</div>'


def render(d: dict) -> str:
    m = d.get("market") or {}
    parts = [
        "<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'>",
        "<meta name='viewport' content='width=device-width,initial-scale=1'>",
        f"<title>区块链早报 · {esc(d.get('date'))}</title>",
        f"<style>{CSS}</style></head><body><main>",
        "<header><h1>区块链早报</h1>",
        f"<div class='sub'>{esc(d.get('date'))} · hanli 为你整理</div></header>",
        "<div class='market'>" +
        chip("BTC", m.get("btc")) + chip("ETH", m.get("eth")) +
        chip("恐惧贪婪指数", m.get("fng")) + "</div>",
    ]

    major = d.get("major") or []
    parts.append(f"<section><h2>重大事件 · {len(major)}</h2>")
    if major:
        for i, it in enumerate(major, 1):
            t = (f"<a href='{esc(it['url'])}' target='_blank'>{esc(it['title'])}</a>"
                 if it.get("url") else esc(it["title"]))
            why = f"<div class='why'>为什么重要：{esc(it['why'])}</div>" if it.get("why") else ""
            parts.append(
                f"<div class='card'><div><span class='no'>{i:02d}</span>"
                f"<span class='t'>{t}</span></div>"
                f"<div class='fact'>{esc(it.get('fact'))}</div>{why}</div>")
    else:
        parts.append("<div class='none'>今日无重大事件收录</div>")
    parts.append("</section>")

    opps = d.get("opps") or []
    parts.append(f"<section><h2>机会雷达 · {len(opps)}</h2>")
    if opps:
        for o in opps:
            rows = "".join(
                f"<div class='row'><span class='k'>{k}</span><span>{esc(v)}</span></div>"
                for k, v in (("做什么", o.get("what")), ("门槛", o.get("entry")),
                             ("风险", o.get("risk"))) if v)
            parts.append(f"<div class='card opp'>{rows}</div>")
    else:
        parts.append("<div class='none'>今日无显著新机会</div>")
    parts.append("</section>")

    if d.get("note"):
        parts.append(f"<div class='note'>📌 {esc(d['note'])}</div>")

    now = datetime.now(SH_TZ).strftime("%Y-%m-%d %H:%M")
    health = d.get("health") or ""
    parts.append(
        f"<footer>源健康：{esc(health)}<br>"
        f"生成于 {now} · 信息仅供研究参考，不构成投资建议 · "
        f"hanli / crypto-news pipeline</footer>")
    parts.append("</main></body></html>")
    return "\n".join(parts)


def validate(d) -> list[str]:
    """Digest JSON schema 校验（接缝①的输出端防御）。

    返回错误列表，空列表 = 通过。缺 major/opps 键容错为空栏；
    title/what 缺失是真错误——卡片没有主体内容没法渲染。
    """
    if not isinstance(d, dict):
        return ["digest 必须是 JSON object"]
    errs = []
    if not isinstance(d.get("date"), str) or not d["date"].strip():
        errs.append("缺少必填字符串字段 date (YYYY-MM-DD)")
    majors = d.get("major", [])
    opps = d.get("opps", [])
    if not isinstance(majors, list):
        errs.append("major 必须是数组")
        majors = []
    if not isinstance(opps, list):
        errs.append("opps 必须是数组")
        opps = []
    for i, it in enumerate(majors):
        if not isinstance(it, dict) or not str(it.get("title", "")).strip():
            errs.append(f"major[{i}] 缺少 title")
    for i, it in enumerate(opps):
        if not isinstance(it, dict) or not str(it.get("what", "")).strip():
            errs.append(f"opps[{i}] 缺少 what(做什么)")
    return errs


def render_to_file(digest: dict, out_dir=None) -> Path:
    out_dir = Path(out_dir) if out_dir else Path(__file__).parent.parent / "out"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{digest.get('date', 'undated').replace('-', '')}区块链早报.html"
    path.write_text(render(digest), encoding="utf-8")
    return path


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--market", help="market.py 输出的 JSON 文件路径")
    args = ap.parse_args()
    digest = json.load(sys.stdin)
    if args.market and Path(args.market).exists():
        digest["market"] = json.load(open(args.market))

    errs = validate(digest)
    if errs:
        print("[fail] digest 校验未通过：", file=sys.stderr)
        for e in errs:
            print("  -", e, file=sys.stderr)
        sys.exit(2)

    print(render_to_file(digest))


if __name__ == "__main__":
    main()
