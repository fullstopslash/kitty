import os
import glob
import subprocess

from kittens.tui.handler import result_handler


def main(args):
	return None


def find_foreground_pid(shell_pid):
	try:
		pts = os.readlink(f'/proc/{shell_pid}/fd/0')
		fd = os.open(pts, os.O_RDONLY | os.O_NOCTTY)
		try:
			fg_pgid = os.tcgetpgrp(fd)
		finally:
			os.close(fd)

		with open(f'/proc/{shell_pid}/stat', 'r') as f:
			shell_stat = f.read()
		rparen = shell_stat.rfind(')')
		shell_fields = shell_stat[rparen + 2:].split()
		shell_pgrp = int(shell_fields[2])

		if fg_pgid == shell_pgrp:
			return shell_pid

		for stat_path in glob.glob('/proc/[0-9]*/stat'):
			try:
				with open(stat_path, 'r') as f:
					data = f.read()
				rp = data.rfind(')')
				fields = data[rp + 2:].split()
				pgrp = int(fields[2])
				pid = int(data[:data.find(' ')])
				if pgrp == fg_pgid and pid != shell_pid:
					return pid
			except (OSError, ValueError, IndexError):
				continue

		return shell_pid
	except Exception:
		return shell_pid


@result_handler(no_ui=True)
def handle_result(args, result, target_window_id, boss):
	window = boss.window_id_map.get(target_window_id)
	if window is None:
		return

	shell_pid = window.child.pid
	fg_pid = find_foreground_pid(shell_pid)

	steal_script = os.path.expanduser('~/projects/cuff/lib/steal-to-tmux.sh')
	if not os.path.isfile(steal_script):
		boss.notify('steal-to-tmux', f'Script not found: {steal_script}', urgency='critical')
		return

	proc = subprocess.run(
		[steal_script, str(fg_pid)],
		capture_output=True,
		text=True,
	)

	if proc.returncode != 0:
		boss.notify(
			'steal-to-tmux',
			proc.stderr.strip() or 'Failed',
			urgency='critical',
		)
		return

	session_name = proc.stdout.strip()
	boss.call_remote_control(
		None,
		(
			'launch',
			'--type=tab',
			'--cwd=current',
			'tmux',
			'attach',
			'-t',
			session_name,
		),
	)
