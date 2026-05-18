#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Transking - 单文件/范围翻译脚本 v4.3
直接调用 19000 内部 LLM 代理，支持块级断点续传与完成后自动质检。
v4.3: 质检仅移除 <think>...</think> 标签及中间内容，不再删除英文分析性语句
"""
import os, re, sys, time, argparse, json, traceback

SCRIPT_VERSION = "4.3"

SYSTEM_PROMPT = (
    "你是一位资深的多领域翻译家。请将文本翻译为中文,风格自然流畅、沉稳老练,"
    "符合中文母语者阅读习惯,准确传达原文情境与逻辑,消除机翻感,严格保留原有分段格式。"
    "人名地名等名词使用业界通用译名。网址、地址、邮箱、电话号码、ISBN、DOI 等特殊信息不要翻译，原样保留。"
    "重要：只输出翻译结果，不要输出任何分析、思考、说明文字。"
)

LLM_BASE_URL = os.environ.get('QCLAW_LLM_BASE_URL', 'http://127.0.0.1:19000/proxy/llm')
LLM_API_KEY  = os.environ.get('QCLAW_LLM_API_KEY', '')

RETRY_MAX        = 3
RETRY_COOLDOWNS  = [60, 180]

# 质检相关正则：仅处理 <think>...</think> 标签
# 成对标签：匹配 <think>...</think> 及中间内容
THINKING_TAG_PAIR = re.compile(r'<think>.*?</think>', re.DOTALL)

def log(msg):
    ts = time.strftime('%H:%M:%S')
    safe = f"[{ts}] {msg}"
    try:
        sys.stdout.write(safe + '\n')
        sys.stdout.flush()
    except UnicodeEncodeError:
        sys.stdout.buffer.write((safe + '\n').encode('utf-8', 'replace'))
        sys.stdout.buffer.flush()

def has_thinking_tags(text):
    """检测文本中是否包含 <think> 和 </think> 标签对"""
    if not text or len(text.strip()) < 20:
        return False
    return bool(THINKING_TAG_PAIR.search(text))

def remove_thinking_tags(text):
    """
    移除 LLM 思考标签及内容：
    1. 成对的 <think>...</think> → 移除标签及中间全部内容
    2. 清理多余空行

    设计原则：宁可保留异常信息，也不误删因审查等原因保留的英文原文。
    """
    if not has_thinking_tags(text):
        return text, False

    original = text

    # 1. 移除成对的 <think>...</think>（含中间内容）
    text = THINKING_TAG_PAIR.sub('', text)

    # 2. 清理多余空行
    text = re.sub(r'\n{3,}', '\n\n', text)

    return text, text != original

def chunk_text(text, target_max=1000):
    paragraphs = text.split('\n\n')
    chunks = []
    buffer = ''
    for para in paragraphs:
        para = para.strip()
        if not para:
            continue
        if len(para) > target_max:
            if buffer.strip():
                chunks.append(buffer.strip())
                buffer = ''
            sentences = re.split(r'(?<=[.!?])\s+', para)
            current = ''
            for sent in sentences:
                if len(current) + len(sent) <= target_max:
                    current += sent + ' '
                else:
                    if current.strip():
                        chunks.append(current.strip())
                    remaining = sent
                    while len(remaining) > target_max:
                        cut = target_max
                        sp = remaining.rfind(' ', 0, target_max)
                        cut = sp if sp > target_max // 2 else cut
                        chunks.append(remaining[:cut])
                        remaining = remaining[cut:]
                    current = remaining + ' '
            if current.strip():
                chunks.append(current.strip())
        else:
            if not buffer:
                buffer = para
            else:
                merged = buffer + '\n\n' + para
                if len(merged) <= target_max:
                    buffer = merged
                else:
                    chunks.append(buffer.strip())
                    buffer = para
    if buffer.strip():
        chunks.append(buffer.strip())
    return chunks

def translate_one_chunk_retry(text, model, timeout=120, out_dir=None):
    import requests
    url = f"{LLM_BASE_URL.rstrip('/')}/chat/completions"
    headers = {"Authorization": f"Bearer {LLM_API_KEY}", "Content-Type": "application/json"}

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": text},
        ],
        "temperature": 0.3,
        "max_tokens": 4096,
        "stream": False,
    }

    for attempt in range(RETRY_MAX):
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=timeout)
            if resp.status_code == 200:
                content = resp.json()['choices'][0]['message']['content'].strip()
                if not content:
                    log("    empty response, retrying...")
                    if attempt < RETRY_MAX - 1:
                        time.sleep(RETRY_COOLDOWNS[attempt])
                        continue
                    else:
                        return text, 'skip'
                return content, 'ok'
            elif resp.status_code == 401:
                return text, 'skip'
            elif resp.status_code == 422:
                return text, 'blocked'
            elif resp.status_code in (403, 429):
                if out_dir:
                    flag_path = os.path.join(out_dir, '_rate_limit_flag')
                    with open(flag_path, 'w') as f:
                        f.write(str(int(time.time())))
                log(f"    HTTP {resp.status_code} - rate limited, exiting. Watchdog will restart.")
                sys.exit(1)
            else:
                if attempt < RETRY_MAX - 1:
                    time.sleep(RETRY_COOLDOWNS[attempt])
                else:
                    return text, 'skip'
        except Exception as e:
            log(f"    Exception: {e}")
            if attempt < RETRY_MAX - 1:
                time.sleep(RETRY_COOLDOWNS[attempt])
            else:
                return text, 'skip'
    return text, 'skip'

def write_project_done_flag(out_dir):
    flag_path = os.path.join(out_dir, '_project_done_flag')
    with open(flag_path, 'w', encoding='utf-8') as f:
        f.write(f"1\n")
    log(f"  完成标志已写入: _project_done_flag")

def fix_llm_thinking(out_dir):
    import glob
    files_fixed = 0
    files = glob.glob(os.path.join(out_dir, '*.txt'))
    for fpath in files:
        try:
            with open(fpath, 'r', encoding='utf-8') as f:
                content = f.read()
        except UnicodeDecodeError:
            with open(fpath, 'r', encoding='utf-8', errors='replace') as f:
                content = f.read()
            log(f"  ⚠ {os.path.basename(fpath)}: 含非法UTF-8字节，已容错读取")
        
        if not has_thinking_tags(content):
            continue
            
        fixed_content, did_fix = remove_thinking_tags(content)
        if did_fix:
            with open(fpath, 'w', encoding='utf-8') as f:
                f.write(fixed_content)
            files_fixed += 1
            log(f"  ✓ 清理 {os.path.basename(fpath)}: <think>标签")

    return files_fixed

def run_post_completion_repair(out_dir):
    log("=" * 50)
    log("开始完成后自动质检修复...")
    
    files_fixed_llm = fix_llm_thinking(out_dir)

    log(f"质检修复完成。共清理 {files_fixed_llm} 个文件。")
    log("=" * 50)
    return {'files_fixed_llm': files_fixed_llm}

def send_completion_notification(out_dir):
    import subprocess, glob
    txt_files = glob.glob(os.path.join(out_dir, '*.txt'))
    msg = f"翻译项目已完成！共处理 {len(txt_files)} 个文件。"

    try:
        result = subprocess.run(['openclaw', 'gateway', 'status'], capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            msg_cmd = [sys.executable, '-c', "import json, os; p=os.path.join(os.path.expanduser('~'), '.qclaw', 'gateway.json'); print(json.load(open(p)).get('port', 18789)) if os.path.exists(p) else print(18789)"]
            port = int(subprocess.run(msg_cmd, capture_output=True, text=True, timeout=5).stdout.strip() or 18789)
            import requests
            requests.post(f"http://127.0.0.1:{port}/api/message", json={"action": "send", "channel": "webchat", "message": msg}, timeout=5)
    except:
        pass

    try:
        with open(os.path.join(out_dir, '_completion_notification.txt'), 'w', encoding='utf-8') as f:
            f.write(msg)
    except:
        pass

def translate_file(src_path, out_dir, model, chunk_max, chunk_delay, error_log, is_last_file=False):
    fname = os.path.basename(src_path)
    out_path = os.path.join(out_dir, fname)
    part_path = out_path + '.part'
    chunks_path = out_path + '.part.chunks'

    # v4.2: 只要输出文件存在即跳过（不判断大小）
    if os.path.exists(out_path):
        log(f"SKIP {fname} (already complete)")
        return True

    try:
        with open(src_path, 'r', encoding='utf-8') as f:
            raw = f.read()
    except UnicodeDecodeError:
        try:
            with open(src_path, 'r', encoding='utf-8', errors='replace') as f:
                raw = f.read()
            log(f"  ⚠ {fname}: 含非法UTF-8字节，已容错读取")
        except Exception as e:
            log(f"ERROR reading {fname}: {e}")
            return False
    except Exception as e:
        log(f"ERROR reading {fname}: {e}")
        return False

    if not raw.strip():
        return True

    chunks = chunk_text(raw, target_max=chunk_max)
    total = len(chunks)
    completed = 0
    
    if os.path.exists(chunks_path):
        try:
            marker = open(chunks_path, 'r').read().strip()
            completed = int(marker.split('/')[0]) if '/' in marker else int(marker)
        except:
            completed = 0

    if completed >= total:
        if os.path.exists(part_path): os.replace(part_path, out_path)
        if os.path.exists(chunks_path): os.remove(chunks_path)
        return True

    for i in range(completed, total):
        chunk = chunks[i]
        idx = i + 1
        is_last_chunk = is_last_file and (idx == total)
        log(f"  [{idx}/{total}] ({len(chunk)} chars)...",)

        translated, status = translate_one_chunk_retry(chunk, model, out_dir=out_dir)

        if status in ['blocked', 'skip']:
            try:
                with open(error_log, 'a', encoding='utf-8') as ef:
                    ef.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {status.upper()}: {fname} chunk {idx}/{total}\n")
            except: pass

        is_first = (i == 0 and completed == 0)
        try:
            with open(part_path, 'w' if is_first else 'a', encoding='utf-8', newline='') as f:
                f.write(translated if is_first else '\n\n' + translated)
                f.flush()
                os.fsync(f.fileno())
        except Exception as e:
            log(f"  write error: {e}")

        try:
            with open(chunks_path, 'w') as cf:
                cf.write(f"{idx}/{total}")
        except: pass

        if is_last_chunk:
            write_project_done_flag(out_dir)

        if idx <= total:
            time.sleep(chunk_delay)

    # .part 重命名为最终文件（提交结果）
    try:
        if os.path.exists(part_path): os.replace(part_path, out_path)
    except Exception as e:
        log(f"  rename error: {e}")

    # 删除 .chunks 进度标记（清理辅助文件）
    try:
        if os.path.exists(chunks_path): os.remove(chunks_path)
    except Exception as e:
        log(f"  cleanup chunks error: {e}")

    log(f"DONE {fname}")
    return True

def main():
    parser = argparse.ArgumentParser(description=f'transking v{SCRIPT_VERSION}')
    parser.add_argument('--source-dir', help='源目录')
    parser.add_argument('--output-dir', required=True, help='输出目录')
    parser.add_argument('--file', help='单个源文件')
    parser.add_argument('--start', type=int, default=1, help='起始文件编号')
    parser.add_argument('--end', type=int, default=9999, help='结束文件编号')
    parser.add_argument('--model', default='pool-deepseek-v4-pro', help='模型名称')
    parser.add_argument('--chunk-max', type=int, default=1500, help='块大小上限')
    parser.add_argument('--chunk-delay', type=int, default=30, help='块间延迟')
    # file-delay 已移除，文件间无额外延迟
    parser.add_argument('--no-post-check', action='store_true', help='跳过自动质检')
    args = parser.parse_args()

    out_dir = args.output_dir
    err_log = os.path.join(out_dir, '_translate_errors.txt')
    os.makedirs(out_dir, exist_ok=True)

    files = [args.file] if args.file else sorted([f for f in os.listdir(args.source_dir) if f.endswith('.txt') and f[0].isdigit()])
    targets = [f for f in files if args.start <= (int(re.match(r'^(\d+)', os.path.basename(f)).group(1)) if re.match(r'^(\d+)', os.path.basename(f)) else 9999) <= args.end]

    log(f"Transking v{SCRIPT_VERSION} | 匹配文件: {len(targets)} | 自动质检: {'关闭' if args.no_post_check else '开启'}")

    for i, fname in enumerate(targets, 1):
        src_path = args.file if args.file else os.path.join(args.source_dir, fname)
        log(f"\n[{i}/{len(targets)}] {fname}")
        translate_file(src_path, out_dir, args.model, args.chunk_max, args.chunk_delay, err_log, is_last_file=(i == len(targets)))

    if not args.no_post_check:
        run_post_completion_repair(out_dir)
    send_completion_notification(out_dir)

if __name__ == '__main__':
    main()
