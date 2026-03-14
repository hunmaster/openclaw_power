"""
ADB를 이용한 IP 변경 모듈

모바일 USB 테더링 환경에서 비행기모드 ON→OFF로 LTE IP를 변경합니다.
- 계정 전환 시 자동 호출
- 1계정 = 1IP 원칙 준수
- 자동화 시작/종료 시 유선 인터넷 비활성화/활성화
"""

import os
import subprocess
import time
import re
import platform

from rich.console import Console

console = Console()


class ADBIPChanger:
    def __init__(self):
        self.adb_path = os.getenv("ADB_PATH", "adb")
        self.airplane_wait = int(os.getenv("ADB_AIRPLANE_WAIT", "4"))
        self.enabled = os.getenv("ADB_IP_CHANGE_ENABLED", "false").lower() == "true"
        self.ethernet_name = os.getenv("ADB_ETHERNET_NAME", "이더넷")
        self.auto_ethernet = os.getenv("ADB_AUTO_ETHERNET", "true").lower() == "true"

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
            return "", 1
        except subprocess.TimeoutExpired:
            console.print("[red]ADB 명령 시간 초과[/red]")
            return "", 1

    def _run_cmd(self, cmd_str):
        """시스템 명령어를 실행합니다 (유선 인터넷 제어용)."""
        try:
            result = subprocess.run(
                cmd_str, capture_output=True, text=True, timeout=10, shell=True
            )
            return result.stdout.strip(), result.returncode
        except Exception as e:
            console.print(f"[red]명령 실행 실패: {e}[/red]")
            return "", 1

    def check_device(self):
        """연결된 ADB 디바이스를 확인합니다."""
        output, code = self._run_adb("devices")
        if code != 0:
            return False, "ADB 실행 실패"

        lines = output.strip().split("\n")
        devices = []
        for line in lines[1:]:
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
        """현재 모바일 IP를 확인합니다 (PC에서 curl로 확인)."""
        # PC에서 curl로 외부 IP 확인 (USB 테더링 상태에서 = 폰 LTE IP)
        try:
            result = subprocess.run(
                ["curl", "-s", "--max-time", "5", "https://api.ipify.org"],
                capture_output=True, text=True, timeout=10
            )
            if result.returncode == 0 and re.match(r"\d+\.\d+\.\d+\.\d+", result.stdout.strip()):
                return result.stdout.strip()
        except Exception:
            pass

        return None

    def disable_ethernet(self):
        """유선 인터넷을 비활성화합니다 (USB 테더링으로 전환)."""
        if not self.auto_ethernet:
            return True, "유선 자동 제어 비활성"

        if platform.system() != "Windows":
            console.print("[yellow]유선 인터넷 제어는 Windows에서만 지원됩니다[/yellow]")
            return False, "Windows 전용 기능"

        console.print(f"[yellow]유선 인터넷 비활성화 중: {self.ethernet_name}[/yellow]")
        _, code = self._run_cmd(f'netsh interface set interface "{self.ethernet_name}" disable')
        if code == 0:
            time.sleep(2)  # 네트워크 전환 대기
            console.print("[green]유선 인터넷 비활성화 완료 → USB 테더링으로 전환됨[/green]")
            return True, "유선 비활성화 완료"
        else:
            console.print(f"[red]유선 비활성화 실패 (관리자 권한 필요)[/red]")
            return False, "유선 비활성화 실패 (관리자 권한으로 실행 필요)"

    def enable_ethernet(self):
        """유선 인터넷을 다시 활성화합니다."""
        if not self.auto_ethernet:
            return True, "유선 자동 제어 비활성"

        if platform.system() != "Windows":
            return False, "Windows 전용 기능"

        console.print(f"[yellow]유선 인터넷 복원 중: {self.ethernet_name}[/yellow]")
        _, code = self._run_cmd(f'netsh interface set interface "{self.ethernet_name}" enable')
        if code == 0:
            console.print("[green]유선 인터넷 복원 완료[/green]")
            return True, "유선 활성화 완료"
        else:
            console.print("[red]유선 활성화 실패[/red]")
            return False, "유선 활성화 실패"

    def toggle_airplane_mode(self):
        """
        비행기모드 ON → OFF로 IP를 변경합니다.
        cmd connectivity 방식 사용 (최신 Android/Samsung 호환)

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

        # 비행기모드 ON (cmd connectivity 방식)
        console.print("[yellow]비행기모드 ON...[/yellow]")
        self._run_adb("shell", "cmd connectivity airplane-mode enable")
        time.sleep(self.airplane_wait)

        # 비행기모드 OFF
        console.print("[yellow]비행기모드 OFF...[/yellow]")
        self._run_adb("shell", "cmd connectivity airplane-mode disable")

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
