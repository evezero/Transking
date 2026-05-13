#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Transking 看门狗 v5.3
每 20 分钟运行一次，双重检测：
1. .part 文件卡住检测（超过 300s 未更新）→ 重启
2. 无 .part + 文件数未对齐 → 重启
"""
import os, sys, time, subprocess, glob, argparse, re

TRANSLATOR_SCRIPT = os.path.join(os.path.dirname(__file__), "auto_translate.py")
STALLED_SECONDS   = 300
LOCK_FILE         = None
LOG_FILE          = None

def log(msg):
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    try:
        print(line)
    except UnicodeEncodeError:
        print(line.encode('utf-8', 'replace').decode('utf-8'))
    if LOG_FILE:
        try:
            with open(LOG_FILE, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except:
            pass

def is_locked(out_dir):
    lock = os.path.join(out_dir, "_watchdog.lock")
    if not os.path.exists(lock):
        return False
    try:
        pid = int(open(lock, "r").read().strip())
        result = subprocess.run(
            ['powershell', '-Command', f"Get-Process -Id {pid} -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Name"],
            capture_output=True, text=True, timeout=5
        )
        if result.stdout.strip():
            return True
        os.remove(lock)
    except:
        pass
    return False

def acquire_lock(out_dir):
    global LOCK_FILE
    LOCK_FILE = os.path.join(out_dir, "_watchdog.lock")
    try:
        open(LOCK_FILE, "w").write(str(os.getpid()))
        return True
    except:
        return False

def release_lock():
    if LOCK_FILE and os.path.exists(LOCK_FILE):
        try: os.remove(LOCK_FILE)
        except: pass

def is_project_done(out_dir):
    flag = os.path.join(out_dir, "_project_done_flag")
    try:
        return os.path.exists(flag) and open(flag, 'r', encoding='utf-8').read().strip() == '1'
    except:
        return False

def notify_completion(out_dir):
    notify_file = os.path.join(out_dir, "_completion_notification.txt")
    msg = ""
    if os.path.exists(notify_file):
        try: msg = open(notify_file, 'r', encoding='utf-8').read().strip()
        except: pass
    if not msg:
        msg = f"翻译项目已完成！共 {len(glob.glob(os.path.join(out_dir, '*.txt')))} 个文件。"
    log(f"✓ {msg}")

def get_part_age(out_dir):
    """返回最新 .part 文件的年龄（秒），不存在返回 None"""
    try:
        part_files = glob.glob(os.path.join(out_dir, "*.txt.part"))
        if not part_files:
            return None
        newest = max(part_files, key=os.path.getmtime)
        return time.time() - os.path.getmtime(newest)
    except:
        return None

def get_part_progress(out_dir):
    """返回 (已完成块数, 总块数)，无法确定返回 (None, None)"""
    try:
        chunks_files = glob.glob(os.path.join(out_dir, "*.txt.part.chunks"))
        if not chunks_files:
            return None, None
        marker = open(max(chunks_files, key=os.path.getmtime), 'r').read().strip()
        if '/' in marker:
            parts = marker.split('/')
            return int(parts[0]), int(parts[1])
        return int(marker), None
    except:
        return None, None

def _extract_number_from_filename(filename):
    """从文件名中提取开头的数字编号"""
    m = re.match(r'^(\d+)', os.path.basename(filename))
    return int(m.group(1)) if m else 0

def infer_start_number(out_dir):
    """
    智能推断起始编号：
    1. 有 .part 文件 → 找修改时间最新的 .part 文件，从其编号开始
    2. 无 .part 文件 → 找序号最大的已完成 .txt 文件，从下一个序号开始
    3. 都没有 → 返回 1
    """
    # 策略1：找最新的 .part 文件
    part_files = glob.glob(os.path.join(out_dir, "*.txt.part"))
    if part_files:
        newest_part = max(part_files, key=os.path.getmtime)
        num = _extract_number_from_filename(newest_part)
        if num > 0:
            log(f"  智能续传：发现 .part 文件 {os.path.basename(newest_part)}，从编号 {num} 开始")
            return num

    # 策略2：找序号最大的已完成 .txt 文件
    txt_files = glob.glob(os.path.join(out_dir, "*.txt"))
    # 排除辅助文件
    txt_files = [f for f in txt_files if not os.path.basename(f).startswith('_')]
    if txt_files:
        max_num = 0
        for fpath in txt_files:
            num = _extract_number_from_filename(fpath)
            if num > max_num:
                max_num = num
        if max_num > 0:
            next_num = max_num + 1
            log(f"  智能续传：已完成到编号 {max_num}，从 {next_num} 开始")
            return next_num

    # 策略3：从头开始
    log("  智能续传：无进度痕迹，从编号 1 开始")
    return 1

def count_source_files(source_dir):
    """统计源目录中需要翻译的 txt 文件数"""
    try:
        return len([f for f in os.listdir(source_dir) if f.endswith('.txt') and f[0].isdigit()])
    except:
        return 0

def count_output_files(out_dir):
    """统计输出目录中已完成的 txt 文件数（排除辅助文件和 .part 文件）"""
    try:
        return len([f for f in os.listdir(out_dir)
                    if f.endswith('.txt') and not f.startswith('_') and f[0].isdigit()])
    except:
        return 0

def has_rate_limit_flag(out_dir):
    """检测速率限制标记是否存在（仅用于日志报告，不影响决策）"""
    return os.path.exists(os.path.join(out_dir, '_rate_limit_flag'))

def kill_python_translator():
    """杀掉所有 auto_translate.py 相关 python 进程"""
    try:
        subprocess.run(
            ['powershell', '-Command',
             "Get-CimInstance Win32_Process -Filter \"name='python.exe'\" | "
             "Where-Object { $_.CommandLine -match 'auto_translate.py' } | "
             "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"],
            capture_output=True, timeout=30
        )
    except:
        pass

def restart_translator(args):
    """清理状态并重启翻译进程，通过 subprocess 运行（避免当前进程环境冲突）"""
    import subprocess

    out_dir = args.output_dir

    # 智能推断起始编号
    inferred_start = infer_start_number(out_dir)

    # 清理完成标记（强制断点续传）
    done_flag = os.path.join(out_dir, "_project_done_flag")
    try:
        if os.path.exists(done_flag): os.remove(done_flag)
    except: pass

    # 杀掉残留进程
    kill_python_translator()

    # 构建命令行参数
    cmd = [
        sys.executable,
        TRANSLATOR_SCRIPT,
        "--source-dir", args.source_dir,
        "--output-dir", out_dir,
        "--start", str(inferred_start),
        "--end", str(args.end),
        "--model", args.model,
        "--chunk-max", str(args.chunk_max),
        "--chunk-delay", str(args.chunk_delay),
    ]
    if args.no_post_check:
        cmd.append("--no-post-check")

    log(f"通过 subprocess 启动翻译进程，从编号 {inferred_start} 开始继续")
    log(f"执行命令: {' '.join(cmd)}")
    try:
        subprocess.run(cmd, cwd=os.path.dirname(TRANSLATOR_SCRIPT), timeout=7200)
        log("翻译进程已结束")
    except subprocess.TimeoutExpired:
        log("翻译进程达到 2 小时超时，退出")
    except Exception as e:
        log(f"翻译出错: {e}")

def main():
    parser = argparse.ArgumentParser(description='Transking 看门狗 v5.3')
    parser.add_argument('--source-dir',  required=True)
    parser.add_argument('--output-dir',  required=True)
    parser.add_argument('--start',       type=int, default=1)
    parser.add_argument('--end',         type=int, default=9999)
    parser.add_argument('--model',       default='pool-deepseek-v4-pro')
    parser.add_argument('--chunk-max',   type=int, default=1500)
    parser.add_argument('--chunk-delay',type=int, default=30)
    # file-delay 已移除
    parser.add_argument('--no-post-check', action='store_true')
    args = parser.parse_args()

    global LOG_FILE
    LOG_FILE = os.path.join(args.output_dir, "_watchdog.log")

    log("=" * 50)
    log("看门狗 v5.3 启动")

    # 锁
    if is_locked(args.output_dir):
        log("已有看门狗运行中，退出")
        return
    if not acquire_lock(args.output_dir):
        log("无法获取锁，退出")
        return

    try:
        # 完成检测
        if is_project_done(args.output_dir):
            notify_completion(args.output_dir)
            return

        source_count = count_source_files(args.source_dir)
        output_count = count_output_files(args.output_dir)
        rate_flag = has_rate_limit_flag(args.output_dir)

        log(f"文件进度: {output_count}/{source_count} | 速率限制标记: {'存在' if rate_flag else '无'}")

        # === 双重检测 ===

        # 检测1: .part 文件卡住
        age = get_part_age(args.output_dir)
        chunk_idx, chunk_total = get_part_progress(args.output_dir)

        if age is not None:
            log(f"  .part 文件: chunk {chunk_idx}/{chunk_total if chunk_total else '?'} | 年龄: {int(age)}s")
            if age > STALLED_SECONDS:
                log(f"⚠ .part 文件卡住（超过 {STALLED_SECONDS}s 未更新），准备重启...")
                restart_translator(args)
                return
            log(f"  .part 文件正常（{int(age)}s 前更新）")
        else:
            # 检测2: 无 .part 文件 + 文件数未对齐 → 重启
            log("  无 .part 文件")
            if output_count < source_count:
                log(f"⚠ 无 .part + 项目未完成（{output_count}/{source_count}），准备重启...")
                restart_translator(args)
                return
            else:
                log("  文件数已对齐，项目可能已完成（等待完成标志）")

        log("一切正常，无需干预")

    finally:
        release_lock()

if __name__ == "__main__":
    main()
