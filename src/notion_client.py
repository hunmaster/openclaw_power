"""
Notion API 연동 모듈
- 데이터베이스에서 댓글 작업 목록 읽기
- 댓글 URL 결과 저장
"""

import os
from dotenv import load_dotenv
from notion_client import Client
from rich.console import Console

console = Console()


class NotionManager:
    def __init__(self):
        # .env 변경사항을 매번 반영
        load_dotenv(override=True)

        self.token = os.getenv("NOTION_API_TOKEN")
        self.database_id = os.getenv("NOTION_DATABASE_ID")

        if not self.token or not self.database_id:
            raise ValueError("NOTION_API_TOKEN과 NOTION_DATABASE_ID를 .env에 설정해주세요.")

        self.client = Client(auth=self.token)

        # 컬럼명 설정 (노션 DB 실제 컬럼명 기준)
        self.col_youtube_url = os.getenv("NOTION_COLUMN_YOUTUBE_URL", "영상 링크")
        self.col_comment_text = os.getenv("NOTION_COLUMN_COMMENT_TEXT", "댓글 원고")
        self.col_result_url = os.getenv("NOTION_COLUMN_COMMENT_RESULT_URL", "댓글 url")
        self.col_status = os.getenv("NOTION_COLUMN_STATUS", "상태")
        self.col_account = os.getenv("NOTION_COLUMN_ACCOUNT", "댓글 계정")
        console.print(f"[dim]컬럼 설정: url='{self.col_youtube_url}', comment='{self.col_comment_text}', "
                      f"result='{self.col_result_url}', status='{self.col_status}', account='{self.col_account}'[/dim]")

    def get_pending_tasks(self):
        """상태가 '댓글작업전'인 작업 목록만 가져옵니다."""
        return self.get_tasks_by_status("댓글작업전")

    def get_tasks_by_status(self, status_value, date_filter=None):
        """지정된 상태의 작업 목록을 가져옵니다. date_filter: 'YYYY-MM-DD' 형식 날짜."""
        console.print(f"[blue]노션 DB 조회 (상태: '{status_value}', 날짜: {date_filter or '전체'})[/blue]")

        # 상태 필터 구성
        status_filter = {"property": self.col_status, "select": {"equals": status_value}}

        # 날짜 필터가 있으면 AND 조건으로 결합
        if date_filter:
            query_filter = {
                "and": [
                    status_filter,
                    {
                        "timestamp": "last_edited_time",
                        "last_edited_time": {"on_or_after": f"{date_filter}T00:00:00+09:00"},
                    },
                    {
                        "timestamp": "last_edited_time",
                        "last_edited_time": {"before": f"{date_filter}T23:59:59+09:00"},
                    },
                ]
            }
        else:
            query_filter = status_filter

        try:
            response = self.client.databases.query(
                database_id=self.database_id,
                page_size=100,
                filter=query_filter,
            )
            console.print(f"[green]select 필터 성공: {len(response.get('results', []))}건[/green]")
        except Exception as e1:
            console.print(f"[yellow]select 필터 실패: {e1}[/yellow]")
            # status 타입으로 재시도
            if date_filter:
                status_filter = {"property": self.col_status, "status": {"equals": status_value}}
                query_filter = {
                    "and": [
                        status_filter,
                        {"timestamp": "last_edited_time", "last_edited_time": {"on_or_after": f"{date_filter}T00:00:00+09:00"}},
                        {"timestamp": "last_edited_time", "last_edited_time": {"before": f"{date_filter}T23:59:59+09:00"}},
                    ]
                }
            else:
                query_filter = {"property": self.col_status, "status": {"equals": status_value}}
            try:
                response = self.client.databases.query(
                    database_id=self.database_id,
                    page_size=100,
                    filter=query_filter,
                )
                console.print(f"[green]status 필터 성공: {len(response.get('results', []))}건[/green]")
            except Exception as e2:
                console.print(f"[yellow]status 필터도 실패: {e2}[/yellow]")
                console.print("[yellow]필터 없이 전체 데이터를 가져옵니다.[/yellow]")
                response = self.client.databases.query(database_id=self.database_id, page_size=100)

        results = response.get("results", [])
        console.print(f"[blue]조회된 결과: {len(results)}건[/blue]")
        console.print(f"[dim]  사용 중인 컬럼명: youtube_url='{self.col_youtube_url}', comment='{self.col_comment_text}', account='{self.col_account}', result_url='{self.col_result_url}'[/dim]")

        tasks = []
        for idx, page in enumerate(results):
            task = self._parse_page(page, debug=(idx == 0))
            if idx < 3:
                console.print(
                    f"[dim]  [{idx}] youtube_url={bool(task.get('youtube_url'))}, "
                    f"comment_text={bool(task.get('comment_text'))}, "
                    f"status={task.get('status')}, "
                    f"url={str(task.get('youtube_url', ''))[:50]}[/dim]"
                )
            if task:
                tasks.append(task)

        console.print(f"[green]'{status_value}' 작업: {len(tasks)}개[/green]")
        return tasks

    def _parse_page(self, page, debug=False):
        """Notion 페이지에서 필요한 데이터를 추출합니다."""
        props = page.get("properties", {})
        task = {"page_id": page["id"]}

        if debug:
            prop_names = {name: prop.get("type", "?") for name, prop in props.items()}
            console.print(f"[dim]  실제 property 이름: {prop_names}[/dim]")

        # 유튜브 링크 추출 - 지정된 컬럼명으로 먼저 시도, 없으면 url 타입 자동 탐색
        youtube_prop = props.get(self.col_youtube_url, {})
        task["youtube_url"] = self._extract_url(youtube_prop)
        if not task["youtube_url"]:
            # url 타입 컬럼을 자동으로 찾기
            for name, prop in props.items():
                if prop.get("type") == "url" and prop.get("url"):
                    task["youtube_url"] = prop.get("url", "")
                    if debug:
                        console.print(f"[yellow]  URL 자동 탐색: '{name}' → {task['youtube_url'][:50]}[/yellow]")
                    break

        # 댓글 원고 추출
        comment_prop = props.get(self.col_comment_text, {})
        task["comment_text"] = self._extract_text(comment_prop)

        # 상태 추출
        status_prop = props.get(self.col_status, {})
        task["status"] = self._extract_status(status_prop)

        # 계정 추출 - 지정된 컬럼명으로 먼저, 없으면 '계정' 포함 컬럼 자동 탐색
        account_prop = props.get(self.col_account, {})
        task["account"] = self._extract_text(account_prop)
        if not task["account"]:
            for name, prop in props.items():
                if "계정" in name and prop.get("type") == "select":
                    task["account"] = self._extract_text(prop)
                    if debug and task["account"]:
                        console.print(f"[yellow]  계정 자동 탐색: '{name}' → {task['account']}[/yellow]")
                    break

        # 기존 댓글 URL 확인 - 지정된 컬럼명으로 먼저, 없으면 '댓글' + 'url' 포함 컬럼 탐색
        result_prop = props.get(self.col_result_url, {})
        task["result_url"] = self._extract_url(result_prop)
        if not task["result_url"]:
            for name, prop in props.items():
                if "댓글" in name and "url" in name.lower():
                    task["result_url"] = self._extract_url(prop) or self._extract_text(prop)
                    if debug and task["result_url"]:
                        console.print(f"[yellow]  댓글URL 자동 탐색: '{name}' → {task['result_url'][:50]}[/yellow]")
                    break

        # 영상 제목 추출 (title 타입 컬럼)
        for name, prop in props.items():
            if prop.get("type") == "title":
                task["video_title"] = self._extract_text(prop)
                break
        if "video_title" not in task:
            task["video_title"] = ""

        # 브랜드 추출
        brand_prop = props.get("브랜드", {})
        task["brand"] = self._extract_text(brand_prop)

        # 최종 편집 일시 추출
        task["last_edited"] = page.get("last_edited_time", "")

        return task

    def _extract_url(self, prop):
        """속성에서 URL을 추출합니다."""
        prop_type = prop.get("type", "")

        if prop_type == "url":
            return prop.get("url", "")
        elif prop_type == "rich_text":
            texts = prop.get("rich_text", [])
            if texts:
                text = texts[0].get("plain_text", "")
                if text.startswith("http"):
                    return text
                # 링크가 href에 있을 수도 있음
                href = texts[0].get("href", "")
                if href:
                    return href
            return ""
        elif prop_type == "title":
            titles = prop.get("title", [])
            if titles:
                text = titles[0].get("plain_text", "")
                if text.startswith("http"):
                    return text
            return ""
        return ""

    def _extract_text(self, prop):
        """속성에서 텍스트를 추출합니다."""
        prop_type = prop.get("type", "")

        if prop_type == "rich_text":
            texts = prop.get("rich_text", [])
            return "".join(t.get("plain_text", "") for t in texts)
        elif prop_type == "title":
            titles = prop.get("title", [])
            return "".join(t.get("plain_text", "") for t in titles)
        elif prop_type == "select":
            select = prop.get("select")
            return select.get("name", "") if select else ""
        return ""

    def _extract_status(self, prop):
        """속성에서 상태를 추출합니다."""
        prop_type = prop.get("type", "")

        if prop_type == "status":
            status = prop.get("status")
            return status.get("name", "") if status else ""
        elif prop_type == "select":
            select = prop.get("select")
            return select.get("name", "") if select else ""
        elif prop_type == "rich_text":
            return self._extract_text(prop)
        return ""

    def _build_result_url_property(self, comment_url):
        """댓글 URL 속성을 DB 컬럼 타입에 맞게 생성합니다 (rich_text 타입)."""
        return {
            "rich_text": [
                {"type": "text", "text": {"content": comment_url, "link": {"url": comment_url}}}
            ]
        }

    def _build_result_url_rich_text(self, comment_url):
        """댓글 URL을 rich_text 형식으로 생성합니다."""
        return {
            "rich_text": [
                {"type": "text", "text": {"content": comment_url, "link": {"url": comment_url}}}
            ]
        }

    def update_task_result(self, page_id, comment_url, status="댓글완료"):
        """댓글 작성 결과를 Notion에 저장합니다."""
        properties = {}

        # 댓글 URL 저장 - url 타입 먼저 시도
        if comment_url:
            properties[self.col_result_url] = self._build_result_url_property(comment_url)

        # 댓글 완료 체크박스 체크
        if status == "댓글완료":
            properties["댓글 완료"] = {"checkbox": True}

        # 상태 업데이트 - select 타입 시도 (실제 DB가 select)
        try:
            properties[self.col_status] = {"select": {"name": status}}
            self.client.pages.update(page_id=page_id, properties=properties)
            console.print(f"[green]Notion 업데이트 완료: {status}[/green]")
        except Exception:
            # url 타입 실패 시 rich_text로 재시도
            if comment_url:
                properties[self.col_result_url] = self._build_result_url_rich_text(comment_url)
            try:
                self.client.pages.update(page_id=page_id, properties=properties)
                console.print(f"[green]Notion 업데이트 완료: {status}[/green]")
            except Exception:
                # status 타입으로도 시도
                try:
                    properties[self.col_status] = {"status": {"name": status}}
                    self.client.pages.update(page_id=page_id, properties=properties)
                    console.print(f"[green]Notion 업데이트 완료: {status}[/green]")
                except Exception as e:
                    # URL만이라도 저장
                    try:
                        url_only = {}
                        if comment_url:
                            url_only[self.col_result_url] = self._build_result_url_rich_text(comment_url)
                        if url_only:
                            self.client.pages.update(page_id=page_id, properties=url_only)
                            console.print(f"[yellow]댓글 URL만 업데이트됨 (상태 실패: {e})[/yellow]")
                    except Exception as e2:
                        console.print(f"[red]Notion 업데이트 실패: {e2}[/red]")

    def update_task_error(self, page_id, error_message):
        """에러 상태를 Notion에 저장합니다."""
        self.update_task_result(page_id, comment_url="", status="에러")
