"""
Notion API 연동 모듈
- 데이터베이스에서 댓글 작업 목록 읽기
- 댓글 URL 결과 저장
"""

import os
from notion_client import Client
from rich.console import Console

console = Console()


class NotionManager:
    def __init__(self):
        self.token = os.getenv("NOTION_API_TOKEN")
        self.database_id = os.getenv("NOTION_DATABASE_ID")

        if not self.token or not self.database_id:
            raise ValueError("NOTION_API_TOKEN과 NOTION_DATABASE_ID를 .env에 설정해주세요.")

        self.client = Client(auth=self.token)

        # 컬럼명 설정
        self.col_youtube_url = os.getenv("NOTION_COLUMN_YOUTUBE_URL", "유튜브 링크")
        self.col_comment_text = os.getenv("NOTION_COLUMN_COMMENT_TEXT", "댓글 원고")
        self.col_result_url = os.getenv("NOTION_COLUMN_COMMENT_RESULT_URL", "댓글 URL")
        self.col_status = os.getenv("NOTION_COLUMN_STATUS", "상태")
        self.col_account = os.getenv("NOTION_COLUMN_ACCOUNT", "계정")

    def get_pending_tasks(self):
        """상태가 '대기' 또는 비어있는 작업 목록을 가져옵니다."""
        try:
            response = self.client.databases.query(
                database_id=self.database_id,
                filter={
                    "or": [
                        {
                            "property": self.col_status,
                            "status": {"equals": "대기"},
                        },
                        {
                            "property": self.col_status,
                            "status": {"is_empty": True},
                        },
                    ]
                },
            )
        except Exception:
            # status 타입이 아닐 수 있으므로 select로 재시도
            try:
                response = self.client.databases.query(
                    database_id=self.database_id,
                    filter={
                        "or": [
                            {
                                "property": self.col_status,
                                "select": {"equals": "대기"},
                            },
                            {
                                "property": self.col_status,
                                "select": {"is_empty": True},
                            },
                        ]
                    },
                )
            except Exception:
                # 필터 없이 전체 조회 후 코드에서 필터링
                console.print("[yellow]상태 필터링 실패, 전체 데이터를 가져옵니다.[/yellow]")
                response = self.client.databases.query(database_id=self.database_id)

        tasks = []
        for page in response.get("results", []):
            task = self._parse_page(page)
            if task and task.get("youtube_url") and task.get("comment_text"):
                # 이미 완료된 항목 제외
                if task.get("status") not in ("완료", "에러"):
                    tasks.append(task)

        console.print(f"[green]대기 중인 작업: {len(tasks)}개[/green]")
        return tasks

    def _parse_page(self, page):
        """Notion 페이지에서 필요한 데이터를 추출합니다."""
        props = page.get("properties", {})
        task = {"page_id": page["id"]}

        # 유튜브 링크 추출
        youtube_prop = props.get(self.col_youtube_url, {})
        task["youtube_url"] = self._extract_url(youtube_prop)

        # 댓글 원고 추출
        comment_prop = props.get(self.col_comment_text, {})
        task["comment_text"] = self._extract_text(comment_prop)

        # 상태 추출
        status_prop = props.get(self.col_status, {})
        task["status"] = self._extract_status(status_prop)

        # 계정 추출
        account_prop = props.get(self.col_account, {})
        task["account"] = self._extract_text(account_prop)

        # 기존 댓글 URL 확인
        result_prop = props.get(self.col_result_url, {})
        task["result_url"] = self._extract_url(result_prop)

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
        """댓글 URL 속성을 DB 컬럼 타입에 맞게 생성합니다."""
        # url 타입 시도, 실패하면 rich_text로 폴백
        return {"url": comment_url}

    def _build_result_url_rich_text(self, comment_url):
        """댓글 URL을 rich_text 형식으로 생성합니다."""
        return {
            "rich_text": [
                {"type": "text", "text": {"content": comment_url, "link": {"url": comment_url}}}
            ]
        }

    def update_task_result(self, page_id, comment_url, status="완료"):
        """댓글 작성 결과를 Notion에 저장합니다."""
        properties = {}

        # 댓글 URL 저장 - url 타입 먼저 시도
        if comment_url:
            properties[self.col_result_url] = self._build_result_url_property(comment_url)

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
