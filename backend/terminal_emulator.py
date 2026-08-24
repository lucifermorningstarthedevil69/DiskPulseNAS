import os
import shlex
import shutil
import time
import psutil
from pathlib import Path
from typing import Dict, List, Any, Tuple
from backend.config import STORAGE_ROOT, format_bytes_short

# ANSI color helper codes
C_RESET = "\033[0m"
C_BOLD = "\033[1m"
C_RED = "\033[31m"
C_GREEN = "\033[32m"
C_YELLOW = "\033[33m"
C_BLUE = "\033[34m"
C_MAGENTA = "\033[35m"
C_CYAN = "\033[36m"
C_WHITE = "\033[37m"

class TerminalSession:
    def __init__(self, session_id: str, root_dir: str = None):
        # Resolve lazily (not at import time) so a storage root chosen
        # later via the setup wizard is picked up for new sessions
        # without requiring a server restart.
        if root_dir is None:
            import backend.config as _config
            root_dir = _config.STORAGE_ROOT
        self.session_id = session_id
        self.root_dir = Path(root_dir).resolve()
        self.current_dir = self.root_dir
        self.history: List[str] = []

    def get_prompt_path(self) -> str:
        try:
            rel = self.current_dir.relative_to(self.root_dir)
            return "~/" + str(rel).replace("\\", "/") if str(rel) != "." else "~"
        except ValueError:
            return "~"

    def execute(self, cmd_line: str) -> Dict[str, Any]:
        cmd_line = cmd_line.strip()
        if not cmd_line:
            return {"output": "", "cwd": self.get_prompt_path(), "exit_code": 0}

        self.history.append(cmd_line)
        
        try:
            parts = shlex.split(cmd_line)
        except Exception as e:
            return {"output": f"{C_RED}Syntax error: {str(e)}{C_RESET}\n", "cwd": self.get_prompt_path(), "exit_code": 1}

        cmd = parts[0].lower()
        args = parts[1:]

        # Command Dispatcher
        handlers = {
            "ls": self._cmd_ls,
            "ll": self._cmd_ll,
            "dir": self._cmd_ls,
            "cd": self._cmd_cd,
            "pwd": self._cmd_pwd,
            "mkdir": self._cmd_mkdir,
            "touch": self._cmd_touch,
            "cat": self._cmd_cat,
            "echo": self._cmd_echo,
            "rm": self._cmd_rm,
            "cp": self._cmd_cp,
            "mv": self._cmd_mv,
            "du": self._cmd_du,
            "df": self._cmd_df,
            "stat": self._cmd_stat,
            "find": self._cmd_find,
            "tree": self._cmd_tree,
            "head": self._cmd_head,
            "tail": self._cmd_tail,
            "grep": self._cmd_grep,
            "top": self._cmd_top,
            "free": self._cmd_free,
            "uname": self._cmd_uname,
            "whoami": self._cmd_whoami,
            "diskpulse": self._cmd_diskpulse,
            "help": self._cmd_help,
            "clear": self._cmd_clear,
            "history": self._cmd_history,
        }

        handler = handlers.get(cmd)
        if not handler:
            return {
                "output": f"{C_RED}diskpulse-sh: command not found: {cmd}{C_RESET}\nType {C_YELLOW}'help'{C_RESET} to view available NAS commands.\n",
                "cwd": self.get_prompt_path(),
                "exit_code": 127
            }

        try:
            output, code = handler(args)
            return {
                "output": output,
                "cwd": self.get_prompt_path(),
                "exit_code": code
            }
        except Exception as e:
            return {
                "output": f"{C_RED}Error: {str(e)}{C_RESET}\n",
                "cwd": self.get_prompt_path(),
                "exit_code": 1
            }

    def _resolve_target(self, path_str: str) -> Path:
        if path_str == "~" or path_str.startswith("~/"):
            rel = path_str[2:] if path_str.startswith("~/") else ""
            target = (self.root_dir / rel).resolve()
        elif os.path.isabs(path_str):
            # Map root to root_dir
            rel = path_str.lstrip("/\\")
            target = (self.root_dir / rel).resolve()
        else:
            target = (self.current_dir / path_str).resolve()
        
        # Security sandbox
        if not str(target).startswith(str(self.root_dir)):
            return self.root_dir
        return target

    def _cmd_ls(self, args: List[str]) -> Tuple[str, int]:
        show_all = "-a" in args or "-la" in args or "-al" in args
        long_format = "-l" in args or "-la" in args or "-al" in args or "-lh" in args
        
        paths = [a for a in args if not a.startswith("-")]
        target = self._resolve_target(paths[0]) if paths else self.current_dir

        if not target.exists():
            return f"{C_RED}ls: cannot access '{paths[0]}': No such file or directory{C_RESET}\n", 1

        if target.is_file():
            return f"{target.name}\n", 0

        items = []
        try:
            for child in sorted(target.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())):
                if not show_all and child.name.startswith("."):
                    continue
                items.append(child)
        except PermissionError:
            return f"{C_RED}ls: cannot open directory '{target.name}': Permission denied{C_RESET}\n", 1

        if long_format:
            lines = [f"total {len(items)}"]
            for item in items:
                stat = item.stat()
                perms = ("d" if item.is_dir() else "-") + "rwxr-xr-x"
                sz = format_bytes_short(stat.st_size) if item.is_file() else "4.0K"
                mtime = time.strftime("%b %d %H:%M", time.localtime(stat.st_mtime))
                color = C_CYAN if item.is_dir() else C_WHITE
                lines.append(f"{perms} 1 nasuser storage {sz:>8} {mtime} {color}{item.name}{C_RESET}")
            return "\n".join(lines) + "\n", 0
        else:
            colored_items = []
            for item in items:
                if item.is_dir():
                    colored_items.append(f"{C_BLUE}{C_BOLD}{item.name}/{C_RESET}")
                else:
                    colored_items.append(f"{C_WHITE}{item.name}{C_RESET}")
            return "  ".join(colored_items) + "\n" if colored_items else "", 0

    def _cmd_ll(self, args: List[str]) -> Tuple[str, int]:
        return self._cmd_ls(["-la"] + args)

    def _cmd_cd(self, args: List[str]) -> Tuple[str, int]:
        if not args or args[0] in ("~", "/"):
            self.current_dir = self.root_dir
            return "", 0
        
        target = self._resolve_target(args[0])
        if not target.exists():
            return f"{C_RED}cd: no such file or directory: {args[0]}{C_RESET}\n", 1
        if not target.is_dir():
            return f"{C_RED}cd: not a directory: {args[0]}{C_RESET}\n", 1

        self.current_dir = target
        return "", 0

    def _cmd_pwd(self, args: List[str]) -> Tuple[str, int]:
        return f"{self.get_prompt_path()}\n", 0

    def _cmd_mkdir(self, args: List[str]) -> Tuple[str, int]:
        if not args:
            return f"{C_RED}mkdir: missing operand{C_RESET}\n", 1
        
        parents = "-p" in args
        paths = [a for a in args if not a.startswith("-")]
        if not paths:
            return f"{C_RED}mkdir: missing operand{C_RESET}\n", 1

        for p in paths:
            target = self._resolve_target(p)
            if target.exists() and not parents:
                return f"{C_RED}mkdir: cannot create directory '{p}': File exists{C_RESET}\n", 1
            target.mkdir(parents=True, exist_ok=True)
        return "", 0

    def _cmd_touch(self, args: List[str]) -> Tuple[str, int]:
        if not args:
            return f"{C_RED}touch: missing file operand{C_RESET}\n", 1
        for p in args:
            target = self._resolve_target(p)
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.exists():
                target.touch()
            else:
                os.utime(str(target), None)
        return "", 0

    def _cmd_cat(self, args: List[str]) -> Tuple[str, int]:
        if not args:
            return f"{C_RED}cat: missing file operand{C_RESET}\n", 1
        target = self._resolve_target(args[0])
        if not target.exists():
            return f"{C_RED}cat: {args[0]}: No such file or directory{C_RESET}\n", 1
        if target.is_dir():
            return f"{C_RED}cat: {args[0]}: Is a directory{C_RESET}\n", 1
        try:
            content = target.read_text(encoding="utf-8", errors="replace")
            return content if content.endswith("\n") else content + "\n", 0
        except Exception as e:
            return f"{C_RED}cat: error reading {args[0]}: {str(e)}{C_RESET}\n", 1

    def _cmd_echo(self, args: List[str]) -> Tuple[str, int]:
        if ">" in args:
            idx = args.index(">")
            text = " ".join(args[:idx])
            dest = args[idx + 1] if idx + 1 < len(args) else ""
            if not dest:
                return f"{C_RED}echo: missing redirect target{C_RESET}\n", 1
            target = self._resolve_target(dest)
            target.write_text(text + "\n", encoding="utf-8")
            return "", 0
        elif ">>" in args:
            idx = args.index(">>")
            text = " ".join(args[:idx])
            dest = args[idx + 1] if idx + 1 < len(args) else ""
            if not dest:
                return f"{C_RED}echo: missing redirect target{C_RESET}\n", 1
            target = self._resolve_target(dest)
            with open(target, "a", encoding="utf-8") as f:
                f.write(text + "\n")
            return "", 0
        return " ".join(args) + "\n", 0

    def _cmd_rm(self, args: List[str]) -> Tuple[str, int]:
        if not args:
            return f"{C_RED}rm: missing operand{C_RESET}\n", 1
        recursive = "-r" in args or "-rf" in args or "-fr" in args
        paths = [a for a in args if not a.startswith("-")]

        for p in paths:
            target = self._resolve_target(p)
            if not target.exists():
                return f"{C_RED}rm: cannot remove '{p}': No such file or directory{C_RESET}\n", 1
            if target == self.root_dir:
                return f"{C_RED}rm: refusing to remove storage root!{C_RESET}\n", 1
            
            if target.is_dir():
                if not recursive:
                    return f"{C_RED}rm: cannot remove '{p}': Is a directory (use -r){C_RESET}\n", 1
                shutil.rmtree(str(target))
            else:
                target.unlink()
        return "", 0

    def _cmd_cp(self, args: List[str]) -> Tuple[str, int]:
        if len(args) < 2:
            return f"{C_RED}cp: missing destination file operand after '{args[0] if args else ''}'{C_RESET}\n", 1
        src = self._resolve_target(args[0])
        dst = self._resolve_target(args[1])
        if not src.exists():
            return f"{C_RED}cp: cannot stat '{args[0]}': No such file or directory{C_RESET}\n", 1
        if dst.is_dir():
            dst = dst / src.name
        if src.is_dir():
            shutil.copytree(str(src), str(dst))
        else:
            shutil.copy2(str(src), str(dst))
        return "", 0

    def _cmd_mv(self, args: List[str]) -> Tuple[str, int]:
        if len(args) < 2:
            return f"{C_RED}mv: missing destination file operand after '{args[0] if args else ''}'{C_RESET}\n", 1
        src = self._resolve_target(args[0])
        dst = self._resolve_target(args[1])
        if not src.exists():
            return f"{C_RED}mv: cannot stat '{args[0]}': No such file or directory{C_RESET}\n", 1
        if dst.is_dir():
            dst = dst / src.name
        shutil.move(str(src), str(dst))
        return "", 0

    def _cmd_du(self, args: List[str]) -> Tuple[str, int]:
        target = self._resolve_target(args[0]) if args and not args[0].startswith("-") else self.current_dir
        if not target.exists():
            return f"{C_RED}du: cannot access '{target.name}': No such file or directory{C_RESET}\n", 1
        
        lines = []
        if target.is_dir():
            total = 0
            for child in sorted(target.iterdir()):
                if child.is_file():
                    sz = child.stat().st_size
                    total += sz
                    lines.append(f"{format_bytes_short(sz):>8}\t{child.name}")
                elif child.is_dir():
                    dir_sz = sum(f.stat().st_size for f in child.rglob('*') if f.is_file())
                    total += dir_sz
                    lines.append(f"{format_bytes_short(dir_sz):>8}\t{child.name}/")
            lines.append(f"{C_BOLD}{format_bytes_short(total):>8}\ttotal{C_RESET}")
        else:
            sz = target.stat().st_size
            lines.append(f"{format_bytes_short(sz):>8}\t{target.name}")
        return "\n".join(lines) + "\n", 0

    def _cmd_df(self, args: List[str]) -> Tuple[str, int]:
        lines = [f"{'Filesystem':<24} {'Size':<10} {'Used':<10} {'Avail':<10} {'Use%':<6} {'Mounted on'}"]
        try:
            for part in psutil.disk_partitions(all=False):
                try:
                    usage = psutil.disk_usage(part.mountpoint)
                    lines.append(
                        f"{part.device:<24} "
                        f"{format_bytes_short(usage.total):<10} "
                        f"{format_bytes_short(usage.used):<10} "
                        f"{format_bytes_short(usage.free):<10} "
                        f"{f'{usage.percent}%':<6} "
                        f"{part.mountpoint}"
                    )
                except Exception:
                    continue
        except Exception:
            pass
        return "\n".join(lines) + "\n", 0

    def _cmd_stat(self, args: List[str]) -> Tuple[str, int]:
        if not args:
            return f"{C_RED}stat: missing operand{C_RESET}\n", 1
        target = self._resolve_target(args[0])
        if not target.exists():
            return f"{C_RED}stat: cannot stat '{args[0]}': No such file or directory{C_RESET}\n", 1
        st = target.stat()
        file_type = "directory" if target.is_dir() else "regular file"
        out = (
            f"  File: {C_BOLD}{target.name}{C_RESET}\n"
            f"  Type: {file_type}\n"
            f"  Size: {st.st_size} bytes ({format_bytes_short(st.st_size)})\n"
            f"Access: {oct(st.st_mode)[-3:]} / (rwxr-xr-x)\n"
            f"Modify: {time.ctime(st.st_mtime)}\n"
            f"Access: {time.ctime(st.st_atime)}\n"
        )
        return out, 0

    def _cmd_find(self, args: List[str]) -> Tuple[str, int]:
        name_filter = None
        if "-name" in args:
            idx = args.index("-name")
            if idx + 1 < len(args):
                name_filter = args[idx + 1].strip('"\'').lower()

        results = []
        for item in self.current_dir.rglob("*"):
            if name_filter:
                if name_filter in item.name.lower():
                    results.append(str(item.relative_to(self.current_dir)).replace("\\", "/"))
            else:
                results.append(str(item.relative_to(self.current_dir)).replace("\\", "/"))
        return ("\n".join(results) + "\n") if results else f"{C_YELLOW}No matching files found.{C_RESET}\n", 0

    def _cmd_tree(self, args: List[str]) -> Tuple[str, int]:
        lines = [f"{C_BLUE}{self.get_prompt_path()}{C_RESET}"]
        
        def walk(directory: Path, prefix: str = ""):
            entries = sorted(list(directory.iterdir()), key=lambda x: (not x.is_dir(), x.name.lower()))
            count = len(entries)
            for i, entry in enumerate(entries):
                is_last = (i == count - 1)
                connector = "└── " if is_last else "├── "
                if entry.is_dir():
                    lines.append(f"{prefix}{connector}{C_BLUE}{entry.name}/{C_RESET}")
                    walk(entry, prefix + ("    " if is_last else "│   "))
                else:
                    lines.append(f"{prefix}{connector}{C_WHITE}{entry.name}{C_RESET}")

        walk(self.current_dir)
        return "\n".join(lines) + "\n", 0

    def _cmd_head(self, args: List[str]) -> Tuple[str, int]:
        if not args:
            return f"{C_RED}head: missing file operand{C_RESET}\n", 1
        n = 10
        paths = []
        i = 0
        while i < len(args):
            if args[i] == "-n" and i + 1 < len(args):
                n = int(args[i+1])
                i += 2
            else:
                paths.append(args[i])
                i += 1
        if not paths:
            return f"{C_RED}head: missing file operand{C_RESET}\n", 1
        target = self._resolve_target(paths[0])
        if not target.exists():
            return f"{C_RED}head: cannot open '{paths[0]}': No such file or directory{C_RESET}\n", 1
        lines = target.read_text(encoding="utf-8", errors="replace").splitlines()[:n]
        return "\n".join(lines) + "\n", 0

    def _cmd_tail(self, args: List[str]) -> Tuple[str, int]:
        if not args:
            return f"{C_RED}tail: missing file operand{C_RESET}\n", 1
        n = 10
        paths = []
        i = 0
        while i < len(args):
            if args[i] == "-n" and i + 1 < len(args):
                n = int(args[i+1])
                i += 2
            else:
                paths.append(args[i])
                i += 1
        if not paths:
            return f"{C_RED}tail: missing file operand{C_RESET}\n", 1
        target = self._resolve_target(paths[0])
        if not target.exists():
            return f"{C_RED}tail: cannot open '{paths[0]}': No such file or directory{C_RESET}\n", 1
        lines = target.read_text(encoding="utf-8", errors="replace").splitlines()[-n:]
        return "\n".join(lines) + "\n", 0

    def _cmd_grep(self, args: List[str]) -> Tuple[str, int]:
        if len(args) < 2:
            return f"{C_RED}grep: pattern and file required{C_RESET}\n", 1
        pattern = args[0]
        target = self._resolve_target(args[1])
        if not target.exists():
            return f"{C_RED}grep: {args[1]}: No such file or directory{C_RESET}\n", 1
        matches = []
        for line in target.read_text(encoding="utf-8", errors="replace").splitlines():
            if pattern in line:
                matches.append(line.replace(pattern, f"{C_RED}{C_BOLD}{pattern}{C_RESET}"))
        return ("\n".join(matches) + "\n") if matches else "", 0

    def _cmd_top(self, args: List[str]) -> Tuple[str, int]:
        cpu = psutil.cpu_percent()
        mem = psutil.virtual_memory()
        lines = [
            f"{C_BOLD}DiskPulse NAS Process & System Monitor{C_RESET}",
            f"CPU Usage: {C_GREEN}{cpu}%{C_RESET} | Mem Usage: {C_GREEN}{mem.percent}%{C_RESET} ({format_bytes_short(mem.used)} / {format_bytes_short(mem.total)})",
            "",
            f"{'PID':<8} {'USER':<10} {'%CPU':<8} {'%MEM':<8} {'COMMAND'}"
        ]
        procs = []
        for p in psutil.process_iter(['pid', 'name', 'username', 'cpu_percent', 'memory_percent']):
            try:
                procs.append(p.info)
            except Exception:
                continue
        # Sort by CPU
        procs = sorted(procs, key=lambda p: (p['cpu_percent'] or 0), reverse=True)[:10]
        for p in procs:
            pid = str(p.get('pid', ''))
            user = (p.get('username') or 'nas')[:9]
            c_pct = f"{(p.get('cpu_percent') or 0):.1f}"
            m_pct = f"{(p.get('memory_percent') or 0):.1f}"
            cmd = (p.get('name') or '')[:30]
            lines.append(f"{pid:<8} {user:<10} {c_pct:<8} {m_pct:<8} {cmd}")
        return "\n".join(lines) + "\n", 0

    def _cmd_free(self, args: List[str]) -> Tuple[str, int]:
        vm = psutil.virtual_memory()
        sw = psutil.swap_memory()
        out = (
            f"               total        used        free      shared  buff/cache   available\n"
            f"Mem:     {format_bytes_short(vm.total):>11} {format_bytes_short(vm.used):>11} {format_bytes_short(vm.free):>11}        0B {format_bytes_short(getattr(vm, 'cached', 0)):>11} {format_bytes_short(vm.available):>11}\n"
            f"Swap:    {format_bytes_short(sw.total):>11} {format_bytes_short(sw.used):>11} {format_bytes_short(sw.free):>11}\n"
        )
        return out, 0

    def _cmd_uname(self, args: List[str]) -> Tuple[str, int]:
        return "Linux diskpulse-nas 6.6.0-nas-generic #1 SMP PREEMPT_DYNAMIC x86_64 GNU/Linux\n", 0

    def _cmd_whoami(self, args: List[str]) -> Tuple[str, int]:
        return "nasadmin\n", 0

    def _cmd_diskpulse(self, args: List[str]) -> Tuple[str, int]:
        banner = (
            f"{C_CYAN}{C_BOLD}"
            r"""
  ____  _     _    ____        _           
 |  _ \(_)___| | _|  _ \ _   _| |___  ___ 
 | | | | / __| |/ / |_) | | | | / __|/ _ \
 | |_| | \__ \   <|  __/| |_| | \__ \  __/
 |____/|_|___/_|\_\_|    \__,_|_|___/\___|
"""
            f"{C_RESET}\n"
            f"{C_GREEN}DiskPulse NAS Storage Hub v1.0.0{C_RESET}\n"
            f"Storage Mount: {self.root_dir}\n"
            f"Status: Healthy | Web UI: http://localhost:8000\n"
        )
        return banner, 0

    def _cmd_history(self, args: List[str]) -> Tuple[str, int]:
        return "\n".join(f"{i+1}  {cmd}" for i, cmd in enumerate(self.history)) + "\n", 0

    def _cmd_clear(self, args: List[str]) -> Tuple[str, int]:
        return "\033[2J\033[H", 0

    def _cmd_help(self, args: List[str]) -> Tuple[str, int]:
        help_text = (
            f"{C_BOLD}DiskPulse Embedded NAS Terminal Shell{C_RESET}\n"
            f"Available file management & diagnostics commands:\n\n"
            f"  {C_GREEN}ls, ll, dir{C_RESET}     List files and directories (-l, -la, -lh, -a)\n"
            f"  {C_GREEN}cd <path>{C_RESET}       Change current working directory (~ for storage root)\n"
            f"  {C_GREEN}pwd{C_RESET}             Print current directory path\n"
            f"  {C_GREEN}mkdir <dir>{C_RESET}     Create new directory (-p supported)\n"
            f"  {C_GREEN}touch <file>{C_RESET}    Create an empty file or update timestamp\n"
            f"  {C_GREEN}cat <file>{C_RESET}      Output file contents\n"
            f"  {C_GREEN}echo <text> [> >> file]{C_RESET} Print or redirect text to file\n"
            f"  {C_GREEN}rm [-r] <path>{C_RESET}  Remove files or directories\n"
            f"  {C_GREEN}cp <src> <dst>{C_RESET}  Copy file or directory\n"
            f"  {C_GREEN}mv <src> <dst>{C_RESET}  Move / rename file or directory\n"
            f"  {C_GREEN}du, df, stat{C_RESET}    Inspect disk usage, filesystem mounts, file stats\n"
            f"  {C_GREEN}find, tree{C_RESET}      Search and visualize directory hierarchy\n"
            f"  {C_GREEN}top, free{C_RESET}       Real-time CPU/RAM/process inspection\n"
            f"  {C_GREEN}diskpulse{C_RESET}       Display DiskPulse NAS banner and status\n"
            f"  {C_GREEN}clear, history{C_RESET}  Clear screen or view command history\n"
        )
        return help_text, 0

# Session manager
sessions: Dict[str, TerminalSession] = {}

def get_or_create_session(session_id: str = "default") -> TerminalSession:
    if session_id not in sessions:
        sessions[session_id] = TerminalSession(session_id)
    return sessions[session_id]
