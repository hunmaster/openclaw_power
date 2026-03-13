"""
YouTube 댓글 자동화 - 메인 오케스트레이터

프로세스:
1. 노션 DB에서 대기 중인 작업 목록 가져오기
2. 안전 규칙 검사 (1일 제한, 시간 간격, 링크 차단, 유사 문구 차단)
3. 각 작업에 대해:
   a. 안티디텍트 지문 설정 (계정별 고유 fingerprint)
   b. 해당 계정의 프록시 설정 (IP 가이드라인)
   c. 시크릿 모드 브라우저 시작
   d. YouTube 로그인
   e. 댓글 작성
   f. 댓글 URL 추출
   g. 노션 DB에 결과 저장
   h. 안전 규칙 기록 (히스토리 저장)
   i. 브라우저 완전 종료 (세션 초기화)
   j. IP 변경 대기

유튜브 바이럴 가이드라인 적용:
- 1일 1계정당 댓글 수 제한 (기본 8개, .env에서 조절 가능)
- 같은 영상 다른 계정 30~60분 간격
- 동일/유사 문구 반복 차단
- 링크 포함 댓글 차단
- 안티디텍트 브라우저 지문으로 계정 간 연결 차단
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
from src.fingerprint import FingerprintManager
from src.safety_rules import SafetyRules

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

    for acc in accounts:
        if acc.get("email", "").startswith(account_label):
            return acc

    return accounts[0] if accounts else None


def display_status(tasks, proxy_manager, safety_rules, accounts):
    """작업 현황을 표시합니다."""
    console.print(Panel("[bold]YouTube 댓글 자동화 프로그램[/bold]", style="blue"))

    # 작업 목록 테이블
    table = Table(title="작업 목록")
    table.add_column("번호", style="cyan", width=5)
    table.add_column("유튜브 URL", style="blue", max_width=50)
    table.add_column("댓글 미리보기", style="white", max_width=30)
    table.add_column("계정", style="green", width=10)

    for i, task in enumerate(tasks, 1):
        comment_preview = (
            task["comment_text"][:30] + "..."
            if len(task["comment_text"]) > 30
            else task["comment_text"]
        )
        table.add_row(
            str(i),
            task["youtube_url"][:50],
            comment_preview,
            task.get("account", "-"),
        )

    console.print(table)

    # 계정별 오늘 사용 현황
    account_table = Table(title="계정별 오늘 댓글 현황")
    account_table.add_column("계정", style="cyan")
    account_table.add_column("오늘 사용", style="yellow")
    account_table.add_column("남은 횟수", style="green")

    shown_accounts = set()
    for task in tasks:
        acc = find_account(accounts, task.get("account"))
        if acc:
            label = acc.get("label", acc.get("email", "unknown"))
            if label not in shown_accounts:
                shown_accounts.add(label)
                status = safety_rules.get_account_status(label)
                account_table.add_row(
                    label,
                    f"{status['today_count']}/{status['max_count']}",
                    str(status["remaining"]),
                )

    console.print(account_table)
    console.print(f"\n[blue]프록시 상태: {proxy_manager.get_status()}[/blue]")


def process_task(task, account, proxy_manager, fingerprint_manager, safety_rules):
    """
    개별 댓글 작업을 처리합니다.

    적용되는 규칙:
    1. 안전 규칙 검사 (1일 제한, 시간 간격, 링크 차단)
    2. 안티디텍트 지문 적용
    3. 계정별 프록시 할당
    4. 시크릿 모드 브라우저
    5. 작업 완료 후 히스토리 기록
    """
    account_label = account.get("label", account.get("email", "unknown"))
    console.print(f"\n[bold blue]━━━ 작업 시작: {account_label} ━━━[/bold blue]")

    # 1. 안전 규칙 검사
    passed, reason = safety_rules.check_all_rules(
        account_label, task["youtube_url"], task["comment_text"]
    )
    if not passed:
        console.print(f"[red]안전 규칙 위반: {reason}[/red]")
        return "SKIP", reason

    # 2. 프록시 설정 (계정별 IP 분리)
    proxy_config = None
    if account.get("account_type") == "sub":
        proxy_url = proxy_manager.get_proxy_for_account(account_label)
        if proxy_url:
            proxy_config = proxy_manager.parse_proxy_for_playwright(proxy_url)

    # 3. 안티디텍트 지문이 적용된 시크릿 모드 브라우저 시작
    bot = YouTubeBot(
        proxy_config=proxy_config,
        fingerprint_manager=fingerprint_manager,
        account_label=account_label,
    )
    try:
        bot.start_browser()

        # 4. YouTube 로그인
        login_success = bot.login_youtube(account["email"], account["password"])
        if not login_success:
            console.print("[red]로그인 실패 - 다음 작업으로 넘어갑니다[/red]")
            return None, "로그인 실패"

        # 5. 댓글 작성 및 URL 추출
        comment_url = bot.post_comment(
            task["youtube_url"],
            task["comment_text"],
        )

        if comment_url:
            # 6. 안전 규칙 히스토리 기록
            safety_rules.record_comment(
                account_label, task["youtube_url"], task["comment_text"]
            )

        return comment_url, None

    except Exception as e:
        console.print(f"[red]작업 중 오류 발생: {e}[/red]")
        return None, str(e)

    finally:
        # 7. 브라우저 완전 종료 (IP 가이드라인: 모든 창 닫기)
        bot.close_browser()


def run():
    """메인 실행 함수"""
    # 환경 변수 로드
    load_dotenv()

    console.print(Panel(
        "[bold green]YouTube 댓글 자동화 프로그램 시작[/bold green]\n"
        "IP 가이드라인 + 유튜브 바이럴 가이드라인 적용\n"
        "안티디텍트 지문 | 프록시 로테이션 | 댓글 안전 규칙",
        style="green",
    ))

    # 모듈 초기화
    try:
        notion = NotionManager()
    except ValueError as e:
        console.print(f"[red]{e}[/red]")
        return

    proxy_manager = ProxyManager()
    fingerprint_manager = FingerprintManager()
    safety_rules = SafetyRules()
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
    display_status(tasks, proxy_manager, safety_rules, accounts)

    # 작업 실행
    delay_ip_change = int(os.getenv("DELAY_AFTER_IP_CHANGE", "3"))
    comment_interval = int(os.getenv("COMMENT_INTERVAL_SEC", "120"))
    success_count = 0
    fail_count = 0
    skip_count = 0
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
        elif prev_account_label == current_label and i > 1:
            # 같은 계정 연속 사용 시 댓글 간격 대기
            console.print(
                f"[yellow]같은 계정 연속 사용 - "
                f"댓글 간격 대기 중... ({comment_interval}초)[/yellow]"
            )
            time.sleep(comment_interval)

        # 작업 실행
        result, error_msg = process_task(
            task, account, proxy_manager, fingerprint_manager, safety_rules
        )

        if result == "SKIP":
            console.print(f"[yellow]⊘ 작업 {i} 건너뜀: {error_msg}[/yellow]")
            skip_count += 1
        elif result:
            comment_url = result if isinstance(result, str) else ""
            notion.update_task_result(task["page_id"], comment_url, status="완료")
            success_count += 1
            console.print(f"[green]작업 {i} 완료[/green]")
        else:
            notion.update_task_error(task["page_id"], error_msg or "댓글 작성 실패")
            fail_count += 1
            console.print(f"[red]작업 {i} 실패[/red]")

        prev_account_label = current_label

    # 결과 요약
    console.print(Panel(
        f"[bold]작업 완료![/bold]\n"
        f"성공: [green]{success_count}[/green]건\n"
        f"실패: [red]{fail_count}[/red]건\n"
        f"건너뜀: [yellow]{skip_count}[/yellow]건\n"
        f"전체: {len(tasks)}건",
        style="blue",
        title="결과 요약",
    ))


if __name__ == "__main__":
    run()
