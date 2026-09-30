#!/usr/bin/env python3
"""Run an argv-based build queue with conservative Linux memory monitoring."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import signal
import subprocess
import time

GIB = 1024 ** 3


def group_rss(pgid):
    total = 0
    for entry in Path('/proc').iterdir():
        if not entry.name.isdigit():
            continue
        try:
            fields = (entry / 'stat').read_text().rsplit(')', 1)[1].split()
            if int(fields[2]) == pgid:
                total += int(fields[21]) * os.sysconf('SC_PAGE_SIZE')
        except (OSError, ValueError, IndexError):
            continue
    return total


def memory_file():
    for path in ['/sys/fs/cgroup/memory/memory.usage_in_bytes', '/sys/fs/cgroup/memory.current']:
        if Path(path).exists():
            return Path(path)
    raise RuntimeError('Cannot locate container memory accounting; refusing to run')


def kill_group(pgid):
    try:
        os.killpg(pgid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def run_step(step, log_path, memory_path, task_limit, container_limit, poll=0.5):
    start = time.monotonic()
    result = {'name': step['name'], 'status': 'running', 'peak_group_rss': 0,
              'peak_container_bytes': 0}
    proc = None
    try:
        usage = int(memory_path.read_text())
        result['peak_container_bytes'] = usage
        if usage >= container_limit:
            result['status'] = 'container_limit_before_start'
            return result
        env = dict(os.environ, LEAN_NUM_THREADS='2', GIT_TERMINAL_PROMPT='0')
        with log_path.open('ab') as log:
            # taskset/nice apply to all descendants without modifying the parent.
            cpus = ','.join(map(str, sorted(os.sched_getaffinity(0))[:4]))
            argv = ['taskset', '-c', cpus, 'nice', '-n', '10', *step['argv']]
            proc = subprocess.Popen(argv, cwd=step['cwd'], env=env, stdout=log,
                                    stderr=subprocess.STDOUT, start_new_session=True)
            while True:
                usage = int(memory_path.read_text())
                rss = group_rss(proc.pid)
                result['peak_group_rss'] = max(result['peak_group_rss'], rss)
                result['peak_container_bytes'] = max(result['peak_container_bytes'], usage)
                if usage >= container_limit:
                    result['status'] = 'container_limit'
                    break
                if rss >= task_limit:
                    result['status'] = 'task_limit'
                    break
                if time.monotonic() - start >= step.get('timeout_seconds', 7200):
                    result['status'] = 'timeout'
                    break
                code = proc.poll()
                if code is not None:
                    result.update(status='success' if code == 0 else 'failed', exit_code=code)
                    break
                time.sleep(poll)
    except BaseException as exc:
        result.update(status='error', error=str(exc))
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            result['status'] = 'cancelled'
    finally:
        if proc is not None:
            kill_group(proc.pid)
            result['exit_code'] = proc.wait()
        result['wall_seconds'] = time.monotonic() - start
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('manifest', type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--wait-for-lock', type=Path, help='Wait for an earlier queue to finish')
    parser.add_argument('--task-gib', type=float, default=32)
    parser.add_argument('--container-gib', type=float, default=100)
    args = parser.parse_args()
    if not 0 < args.task_gib < args.container_gib:
        parser.error('Require 0 < task-gib < container-gib')
    args.output.mkdir(parents=True, exist_ok=True)
    lock = (args.output / 'queue.lock').open('w')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    if args.wait_for_lock:
        with args.wait_for_lock.open('a') as previous:
            print('Waiting for preceding queue to release its lock.', flush=True)
            fcntl.flock(previous, fcntl.LOCK_EX)
    mem = memory_file()
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    steps = json.loads(args.manifest.read_text())['steps']
    for index, step in enumerate(steps):
        print(f"START {index}: {step['name']}", flush=True)
        result = run_step(step, args.output / f'{index:02d}.log', mem,
                          args.task_gib * GIB, args.container_gib * GIB)
        (args.output / f'{index:02d}.json').write_text(json.dumps(result, indent=2) + '\n')
        print(json.dumps(result), flush=True)
        if result['status'] != 'success':
            print('Queue stopped; inspect logs before manually resuming.', flush=True)
            return 1
    print('Queue completed successfully.', flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
