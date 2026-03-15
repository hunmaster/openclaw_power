"""사용자 데이터베이스 모델."""
from datetime import datetime
from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash

db = SQLAlchemy()


class User(UserMixin, db.Model):
    __tablename__ = "users"

    VALID_PLANS = ["Free", "Starter", "Business", "Agency", "Enterprise"]

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    nickname = db.Column(db.String(50), nullable=True)
    license_key = db.Column(db.String(255), nullable=True)
    plan = db.Column(db.String(50), nullable=False, default="Free")
    is_active_user = db.Column(db.Boolean, default=True)
    agreed_terms = db.Column(db.Boolean, default=False)
    agreed_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    last_login = db.Column(db.DateTime, nullable=True)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    @property
    def is_active(self):
        return self.is_active_user

    # 관계
    youtube_accounts = db.relationship("YouTubeAccount", backref="owner", lazy="dynamic")

    def to_dict(self):
        return {
            "id": self.id,
            "email": self.email,
            "nickname": self.nickname,
            "plan": self.plan or "Free",
            "license_key": (self.license_key or "")[:8] + "..." if self.license_key else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "last_login": self.last_login.isoformat() if self.last_login else None,
        }


class YouTubeAccount(db.Model):
    """유저별 유튜브 계정 저장 (영속성)."""
    __tablename__ = "youtube_accounts"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    account_email = db.Column(db.String(255), nullable=False)
    label = db.Column(db.String(100), nullable=True)
    account_type = db.Column(db.String(50), default="google")
    cookies_saved = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id,
            "email": self.account_email,
            "label": self.label or self.account_email.split("@")[0],
            "account_type": self.account_type,
            "cookies_saved": self.cookies_saved,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class CommentTracking(db.Model):
    """유저별 댓글 트래킹 데이터 영속 저장."""
    __tablename__ = "comment_tracking"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    video_url = db.Column(db.String(500), nullable=False)
    video_title = db.Column(db.String(500), nullable=True)
    comment_text = db.Column(db.Text, nullable=True)
    status = db.Column(db.String(50), default="tracking")  # tracking, exposed, lost
    last_checked = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id,
            "video_url": self.video_url,
            "video_title": self.video_title,
            "comment_text": self.comment_text,
            "status": self.status,
            "last_checked": self.last_checked.isoformat() if self.last_checked else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
