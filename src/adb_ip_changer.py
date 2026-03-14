"""
ADB를 이용한 IP 변경 모듈

모바일 핫스팟/테더링 환경에서 비행기모드 ON→OFF로 LTE IP를 변경합니다.
- 계정 전환 시 자동 호출
- 1계정 = 1IP 원칙 준수
"""

import os
import subprocess
import time
import re

from rich.console import Console

console = Console()


class ADBIPChanger:
    def __init__(self):
        self.adb_path = os.getenv("ADB_PATH", "adb")
        self.airplane_wait = int(os.getenv("ADB_AIRPLANE_WAIT", "4"))
        self.enabled = os.getenv("ADB_IP_CHANGE_ENABLED", "false").lower() == "true"

    def _run_adb(self, *args):
        """ADB 명령어를 실행하고 결과를 반환합니다."""
        cmd = [self.adb_path] + list(args)
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=15
            )
            return result.stdout.strip(), result.returncode
        except FileNotFoundError:
            console.print(f"[red]ADB를 찾을 수 없습니다: {self.adb_path}[/red]")
            console.print("[yellow]ADB_PATH 환경변수를 설정하거나 PATH에 추가하세요.[/yellow]")
            return "", 1
        except subprocess.TimeoutExpired:
            console.print("[red]ADB 명령 시간 초과[/red]")
            return "", 1

    def check_device(self):
        """연결된 ADB 디바이스를 확인합니다."""
        output, code = self._run_adb("devices")
        if code != 0:
            return False, "ADB 실행 실패"

        lines = output.strip().split("\n")
        devices = []
        for line in lines[1:]:  # 첫 줄은 "List of devices attached"
            parts = line.strip().split("\t")
            if len(parts) == 2:
                serial, status = parts
                devices.append({"serial": serial, "status": status})

        if not devices:
            return False, "연결된 디바이스 없음"

        for d in devices:
            if d["status"] == "device":
                console.print(f"[green]ADB 디바이스 연결됨: {d['serial']}[/green]")
                return True, d["serial"]
            elif d["status"] == "unauthorized":
                return False, f"디바이스 인증 필요 (USB 디버깅 허용): {d['serial']}"

        return False, f"디바이스 상태 이상: {devices}"

    def get_current_ip(self):
        """현재 모바일 IP를 확인합니다."""
        # 방법 1: ifconfig로 rmnet (LTE) IP 확인
        output, code = self._run_adb("shell", "ifconfig rmnet_data0 2>/dev/null || ifconfig rmnet0 2>/dev/null")
        if code == 0 and output:
            match = re.search(r"inet addr:(\d+\.\d+\.\d+\.\d+)", output)
            if match:
                return match.group(1)

        # 방법 2: ip addr로 확인
        output, code = self._run_adb("shell", "ip addr show 2>/dev/null | grep 'inet ' | grep -v '127.0.0.1'")
        if code == 0 and output:
            match = re.search(r"inet (\d+\.\d+\.\d+\.\d+)", output)
            if match:
                return match.group(1)

        # 방법 3: curl로 외부 IP 확인
        output, code = self._run_adb("shell", "curl -s --max-time 5 https://api.ipify.org 2>/dev/null")
        if code == 0 and output and re.match(r"\d+\.\d+\.\d+\.\d+", output):
            return output.strip()

        return None

    def toggle_airplane_mode(self):
        """
        비행기모드 ON → OFF로 IP를 변경합니다.

        Returns:
            (bool, str): (성공여부, 메시지)
        """
        if not self.enabled:
            return False, "ADB IP 변경이 비활성화되어 있습니다"

        # 디바이스 확인
        connected, info = self.check_device()
        if not connected:
            return False, f"ADB 디바이스 연결 실패: {info}"

        old_ip = self.get_current_ip()
        console.print(f"[blue]현재 IP: {old_ip or '확인 불가'}[/blue]")

        # 비행기모드 ON
        console.print("[yellow]비행기모드 ON...[/yellow]")
        self._run_adb("shell", "settings put global airplane_mode_on 1")
        self._run_adb("shell", "am broadcast -a android.intent.action.AIRPLANE_MODE --ez state true")
        time.sleep(self.airplane_wait)

        # 비행기모드 OFF
        console.print("[yellow]비행기모드 OFF...[/yellow]")
        self._run_adb("shell", "settings put global airplane_mode_on 0")
        self._run_adb("shell", "am broadcast -a android.intent.action.AIRPLANE_MODE --ez state false")

        # 네트워크 재연결 대기
        console.print("[yellow]네트워크 재연결 대기 중...[/yellow]")
        time.sleep(self.airplane_wait)

        # 새 IP 확인
        new_ip = self.get_current_ip()
        console.print(f"[blue]새 IP: {new_ip or '확인 불가'}[/blue]")

        if old_ip and new_ip and old_ip != new_ip:
            console.print(f"[green]IP 변경 성공: {old_ip} → {new_ip}[/green]")
            return True, f"IP 변경 완료: {old_ip} → {new_ip}"
        elif new_ip:
            console.print(f"[yellow]IP 변경 확인 불가 (새 IP: {new_ip})[/yellow]")
            return True, f"비행기모드 토글 완료 (IP: {new_ip})"
        else:
            console.print("[yellow]IP 확인 불가 - 비행기모드 토글은 완료됨[/yellow]")
            return True, "비행기모드 토글 완료 (IP 확인 불가)"

    def get_status(self):
        """ADB IP 변경 모듈 상태를 반환합니다."""
        if not self.enabled:
            return {"enabled": False, "device": None, "ip": None}

        connected, info = self.check_device()
        ip = self.get_current_ip() if connected else None
        return {
            "enabled": True,
            "device": info if connected else None,
            "device_connected": connected,
            "device_message": info,
            "ip": ip,
        }
