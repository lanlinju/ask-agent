import subprocess
import platform
import os


class PersistentShell:
    """跨平台持久化 shell 会话"""
    
    def __init__(self):
        self.is_windows = platform.system() == "Windows"
        self.marker = f"END_{os.getpid()}"
        
        if self.is_windows:
            # Windows: 使用 PowerShell
            self.process = subprocess.Popen(
                ["powershell.exe", "-NoLogo", "-NoProfile", "-Command", "-"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
        else:
            # Linux/Mac: 使用 bash
            self.process = subprocess.Popen(
                ["/bin/bash"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
    
    def execute(self, cmd, timeout=10):
        """执行命令并返回输出"""
        self.process.stdin.write(cmd + "\n")
        self.process.stdin.write(f"echo {self.marker}\n")
        self.process.stdin.flush()
        
        output = ""
        while True:
            line = self.process.stdout.readline()
            if not line:
                break
            if self.marker in line:
                break
            output += line
        
        return output
    
    def close(self):
        """关闭 shell"""
        exit_cmd = "exit\n"
        self.process.stdin.write(exit_cmd)
        self.process.stdin.flush()
        self.process.wait()
        self.process = None


if __name__ == "__main__":
    shell = PersistentShell()
    print("--- Test 1 ---")
    print(shell.execute("echo hello world"))
    print("--- Test 2 ---")
    print(shell.execute("pwd"))
    print("Interrupt test done")
    
    shell.close()
