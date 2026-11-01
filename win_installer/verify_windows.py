"""review release acceptance on disposable directories, using the actual installer."""
import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
import time
import urllib.request

ROOT = Path.cwd().resolve()


def shortcuts(env):
    command = r'''[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false); $w=New-Object -ComObject WScript.Shell; $d=$w.SpecialFolders.Item('Desktop');
    $rows=foreach($name in @('启动言行.lnk','停止言行.lnk','卸载言行.lnk')) {
      $p=Join-Path (Join-Path $d 'other') $name;
      if(Test-Path -LiteralPath $p){$s=$w.CreateShortcut($p);[pscustomobject]@{name=$name;target=$s.TargetPath;args=$s.Arguments}}
    }; [pscustomobject]@{desktop=$d;links=@($rows)}|ConvertTo-Json -Depth 4 -Compress'''
    p = subprocess.run([os.environ['SystemRoot'] + r'\System32\WindowsPowerShell\v1.0\powershell.exe',
                        '-NoProfile', '-NonInteractive', '-Command', command],
                       capture_output=True, text=True, encoding='utf-8', env={**env, 'PYTHONUTF8': '1'}, timeout=30)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)


def run(artifact, work, phase):
    work = Path(work).resolve()
    if not work.is_relative_to(ROOT / '.cache' / 'installer-20261026'):
        raise ValueError('验收只能使用本项目安装验收缓存目录。')
    work.mkdir(parents=True, exist_ok=True)
    target = work / '全新安装目录'
    local = work / 'clean-user' / 'Local'
    data = local / '言行' / 'data'
    env = {k: v for k, v in os.environ.items() if k.upper() not in {
        'PYTHONHOME', 'PYTHONPATH', 'VIRTUAL_ENV', 'DEEPSEEK_API_KEY', 'OPENAI_API_KEY',
        'ZHIPU_API_KEY', 'DASHSCOPE_API_KEY', 'ARK_API_KEY', 'MOONSHOT_API_KEY'}}
    env.update(PATH=os.environ['SystemRoot'] + r'\System32', LOCALAPPDATA=str(local),
               APPDATA=str(work / 'clean-user' / 'Roaming'), PYTHONUTF8='1')
    python = target / 'python' / 'python.exe'
    record = work / 'acceptance.json'
    result = json.loads(record.read_text(encoding='utf-8')) if record.exists() else {}
    if phase in ('install', 'upgrade'):
        before = {p.relative_to(data).as_posix(): sha256(p.read_bytes()).hexdigest()
                  for p in data.rglob('*') if p.is_file()} if data.exists() else {}
        p = subprocess.run(f'"{Path(artifact).resolve()}" /S /D={target}', env=env, timeout=180)
        assert p.returncode == 0, p.returncode
        assert python.is_file(), '安装目标不符或内置 Python 缺失'
        for name, digest in before.items():
            assert sha256((data / name).read_bytes()).hexdigest() == digest, name
        probe = subprocess.run([str(python), '-B', '-X', 'utf8', '-c',
            'import sys,os,streamlit,pyotp,qrcode,openpyxl;from pathlib import Path;'
            'root=Path(sys.argv[1]).resolve();'
            'assert Path(sys.executable).resolve()==root/"python"/"python.exe";'
            'assert all(Path(p).resolve().is_relative_to(root) for p in sys.path if p);'
            'from bank.seed import demo_path;from agent.model_credentials import PROFILE;'
            'data=Path(os.environ["LOCALAPPDATA"])/"言行"/"data";'
            'assert demo_path()==data/"runtime"/"sandbox.sqlite3";'
            'assert PROFILE==data/".cache"/"bank-model"/"profile.json";'
            'print(sys.version);print(streamlit.__version__);print(sys.path)', str(target)],
            capture_output=True, text=True, env=env, cwd=work, timeout=40)
        assert probe.returncode == 0, probe.stderr
        assert '.venv' not in probe.stdout
        links = shortcuts(env)
        assert len(links['links']) == 3, links
        assert not (Path(links['desktop']) / '言行.lnk').exists()
        for link in links['links']:
            expected = target / ('uninstall.exe' if link['name'] == '卸载言行.lnk' else 'python/pythonw.exe')
            assert Path(link['target']) == expected, link
            if link['name'] != '卸载言行.lnk':
                assert link['args'].startswith('-B '), link
        result[phase] = dict(passed=True, runtime=probe.stdout, previous_user_files=len(before), shortcuts=links)
    elif phase == 'start':
        with (work / 'launch.log').open('ab') as log:
            child = subprocess.Popen([str(python), '-B', '-X', 'utf8', str(target / 'launcher' / 'launch.py'),
                                      '--port', '8526', '--no-browser'],
                                     env=env, cwd=work, stdout=log, stderr=log,
                                     creationflags=subprocess.CREATE_NO_WINDOW)
        for _ in range(200):
            assert child.poll() is None, '启动器提前退出'
            try:
                with urllib.request.urlopen('http://127.0.0.1:8526/_stcore/health', timeout=.5) as response:
                    if response.read() == b'ok':
                        break
            except OSError:
                pass
            time.sleep(.2)
        else:
            raise AssertionError('安装后启动超时')
        second = subprocess.run([str(python), '-B', str(target / 'launcher' / 'launch.py'),
                                 '--port', '8526', '--no-browser'], env=env, cwd=work, timeout=20)
        assert second.returncode == 0
        result['start'] = dict(passed=True, pid=child.pid, duplicate_launch_reused=True,
                               url='http://127.0.0.1:8526', data=str(data))
    elif phase == 'stop':
        p = subprocess.run([str(python), '-B', str(target / 'launcher' / 'stop.py'), '--quiet'],
                           env=env, cwd=work, timeout=35)
        assert p.returncode == 0, p.returncode
        assert not (data / 'launcher.json').exists()
        result['stop'] = dict(passed=True)
    elif phase == 'uninstall':
        (data / 'preservation-marker.txt').write_text('test data; default uninstall must preserve', encoding='utf-8')
        before = {p.relative_to(data).as_posix(): sha256(p.read_bytes()).hexdigest()
                  for p in data.rglob('*') if p.is_file()}
        p = subprocess.run(f'"{target / "uninstall.exe"}" /S _?={target}', env=env, timeout=120)
        assert p.returncode == 0
        assert not python.exists(), '运行时未卸载'
        assert not shortcuts(env)['links'], '本应用快捷方式未卸载'
        for name, digest in before.items():
            assert sha256((data / name).read_bytes()).hexdigest() == digest, name
        result['uninstall'] = dict(passed=True, retained_user_files=len(before))
    else:
        raise ValueError(phase)
    record.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(dict(phase=phase, passed=True), ensure_ascii=False))


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('phase', choices=['install', 'start', 'stop', 'upgrade', 'uninstall'])
    p.add_argument('--artifact', required=True)
    p.add_argument('--work', required=True)
    args = p.parse_args()
    run(args.artifact, args.work, args.phase)
