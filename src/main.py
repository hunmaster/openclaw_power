"""
YouTube 댓글 자동화 - 메인 오케스트레이터

프로세스:
1. 노션 DB에서 대기 중인 작업 목록 가져오기
2. 각 작업에 대해:
   a. 해당 계정의 프록시 설정 (IP 가이드라인)
   b. 시크릿 모드 브라우저 시작
   c. YouTube 로그인
   d. 댓글 작성
   e. 댓글 URL 추출
   f. 노션 DB에 결과 저장
   g. 브라우저 완전 종료 (세션 초기화)
   h. IP 변경 대기 (비행기모드 시뮬레이션)
3. 계정 전환 시 프록시 변경 (IP 변경)
"""

import os
import sys
import json
import time

from dotenv import load_dotenv
from rich.console import Console
from rich.table import Table
from rich.panel import Panel

# 프로젝트 루트를 경로에 추가
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.notion_client import NotionManager
from src.proxy_manager import ProxyManager
from src.youtube_bot import YouTubeBot

console = Console()


def load_accounts():
    """계정 정보를 JSON 파일에서 로드합니다."""
    accounts_file = os.getenv("ACCOUNTS_FILE", "config/accounts.json")

    if not os.path.exists(accounts_file):
        console.print(f"[red]계정 파일을 찾을 수 없습니다: {accounts_file}[/red]")
        console.print("[yellow]config/accounts.example.json을 참고하여 accounts.json을 생성하세요.[/yellow]")
        return []

    with open(accounts_file, "r") as f:
        accounts = json.load(f)

    console.print(f"[green]계정 {len(accounts)}개 로드됨[/green]")
    return accounts


def find_account(accounts, account_label):
    """라벨로 계정을 찾습니다."""
    if not account_label:
        return accounts[0] if accounts else None

    for acc in accounts:
        if acc.get("label") == account_label:
            return acc

    # 라벨이 없으면 이메일로 검색
    for acc in accounts:
        if acc.get("email", "").startswith(account_label):
            return acc

    return accounts[0] if accounts else None


def display_status(tasks, proxy_manager):
    """작업 현황을 표시합니다."""
    console.print(Panel("[bold]YouTube 댓글 자동화 프로그램[/bold]", style="blue"))

    table = Table(title="작업 목록")
    table.add_column("번호", style="cyan", width=5)
    table.add_column("유튜브 URL", style="blue", max_width=50)
    table.add_column("댓글 미리보기", style="white", max_width=30)
    table.add_column("계정", style="green", width=10)

    for i, task in enumerate(tasks, 1):
        comment_preview = task["comment_text"][:30] + "..." if len(task["comment_text"]) > 30 else task["comment_text"]
        table.add_row(
            str(i),
            task["youtube_url"][:50],
            comment_preview,
            task.get("account", "-"),
        )

    console.print(table)
    console.print(f"\n[blue]프록시 상태: {proxy_manager.get_status()}[/blue]")


def process_task(task, account, proxy_manager):
    """
    개별 댓글 작업을 처리합니다.

    IP 가이드라인 적용:
    1. 계정별 프록시 할당 (부계정은 각각 다른 IP)
    2. 시크릿 모드 브라우저 사용
    3. 작업 완료 후 브라우저 완전 종료
    4. IP 변경 대기 시간 적용
    """
    account_label = account.get("label", account.get("email", "unknown"))
    console.print(f"\n[bold blue]━━━ 작업 시작: {account_label} ━━━[/bold blue]")

    # 1. 프록시 설정 (계정별 IP 분리)
    proxy_config = None
    if account.get("account_type") == "sub":
        proxy_url = proxy_manager.get_proxy_for_account(account_label)
        if proxy_url:
            proxy_config = proxy_manager.parse_proxy_for_playwright(proxy_url)

    # 2. 시크릿 모드 브라우저 시작
    bot = YouTubeBot(proxy_config=proxy_config)
    try:
        bot.start_browser()

        # 3. YouTube 로그인
        login_success = bot.login_youtube(account["email"], account["password"])
        if not login_success:
            console.print("[red]로그인 실패 - 다음 작업으로 넘어갑니다[/red]")
            return False

        # 4. 댓글 작성 및 URL 추출
        comment_url = bot.post_comment(
            task["youtube_url"],
            task["comment_text"],
        )

        return comment_url

    except Exception as e:
        console.print(f"[red]작업 중 오류 발생: {e}[/red]")
        return None

    finally:
        # 5. 브라우저 완전 종료 (IP 가이드라인: 모든 창 닫기)
        bot.close_browser()


def run():
    """메인 실행 함수"""
    # 환경 변수 로드
    load_dotenv()

    console.print(Panel(
        "[bold green]YouTube 댓글 자동화 프로그램 시작[/bold green]\n"
        "IP 가이드라인 적용: 계정별 프록시 분리, 시크릿 모드",
        style="green",
    ))

    # 모듈 초기화
    try:
        notion = NotionManager()
    except ValueError as e:
        console.print(f"[red]{e}[/red]")
        return

    proxy_manager = ProxyManager()
    accounts = load_accounts()

    if not accounts:
        console.print("[red]사용 가능한 계정이 없습니다. config/accounts.json을 확인해주세요.[/red]")
        return

    # 대기 중인 작업 가져오기
    tasks = notion.get_pending_tasks()
    if not tasks:
        console.print("[yellow]대기 중인 작업이 없습니다.[/yellow]")
        return

    # 현황 표시
    display_status(tasks, proxy_manager)

    # 작업 실행
    delay_ip_change = int(os.getenv("DELAY_AFTER_IP_CHANGE", "3"))
    success_count = 0
    fail_count = 0
    prev_account_label = None

    for i, task in enumerate(tasks, 1):
        console.print(f"\n[bold]═══ 작업 {i}/{len(tasks)} ═══[/bold]")

        # 사용할 계정 결정
        account = find_account(accounts, task.get("account"))
        if not account:
            console.print("[red]사용 가능한 계정이 없습니다.[/red]")
            notion.update_task_error(task["page_id"], "계정 없음")
            fail_count += 1
            continue

        current_label = account.get("label", account.get("email"))

        # 계정 전환 시 IP 변경 대기 (비행기모드 시뮬레이션)
        if prev_account_label and prev_account_label != current_label:
            console.print(
                f"[yellow]계정 전환: {prev_account_label} → {current_label}[/yellow]"
            )
            console.print(
                f"[yellow]IP 변경 대기 중... ({delay_ip_change}초)[/yellow]"
            )
            time.sleep(delay_ip_change)

        # 작업 실행
        result = process_task(task, account, proxy_manager)

        if result:
            comment_url = result if isinstance(result, str) else ""
            notion.update_task_result(task["page_id"], comment_url, status="완료")
            success_count += 1
            console.print(f"[green]✓ 작업 {i} 완료[/green]")
        else:
            notion.update_task_error(task["page_id"], "댓글 작성 실패")
            fail_count += 1
            console.print(f"[red]✗ 작업 {i} 실패[/red]")

        prev_account_label = current_label

    # 결과 요약
    console.print(Panel(
        f"[bold]작업 완료![/bold]\n"
        f"성공: [green]{success_count}[/green]건\n"
        f"실패: [red]{fail_count}[/red]건\n"
        f"전체: {len(tasks)}건",
        style="blue",
        title="결과 요약",
    ))


if __name__ == "__main__":
    run()
