from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime, Boolean, Text, ForeignKey
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship
from datetime import datetime

DATABASE_URL = "sqlite:///./reviewradar.db"
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True, nullable=False)
    hashed_password = Column(String, nullable=False)
    business_name = Column(String, nullable=False)
    plan = Column(String, default="free")  # free, starter, pro
    stripe_customer_id = Column(String, nullable=True)
    stripe_subscription_id = Column(String, nullable=True)
    email_notifications = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    connections = relationship("PlatformConnection", back_populates="user")
    reviews = relationship("Review", back_populates="user")


class PlatformConnection(Base):
    __tablename__ = "platform_connections"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    platform = Column(String, nullable=False)  # google, facebook, tripadvisor
    access_token = Column(String, nullable=True)
    refresh_token = Column(String, nullable=True)
    account_id = Column(String, nullable=True)  # e.g. Google location ID
    account_name = Column(String, nullable=True)
    connected_at = Column(DateTime, default=datetime.utcnow)

    user = relationship("User", back_populates="connections")


class Review(Base):
    __tablename__ = "reviews"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    platform = Column(String, nullable=False)
    external_id = Column(String, nullable=True)  # ID on the platform
    author_name = Column(String, nullable=True)
    rating = Column(Float, nullable=True)
    text = Column(Text, nullable=True)
    reply = Column(Text, nullable=True)
    replied_at = Column(DateTime, nullable=True)
    review_date = Column(DateTime, nullable=True)
    fetched_at = Column(DateTime, default=datetime.utcnow)
    notified = Column(Boolean, default=False)

    user = relationship("User", back_populates="reviews")


# Create all tables
Base.metadata.create_all(bind=engine)
