"""
ReviewRadar — FastAPI Backend
Main application with all API routes.
"""
from fastapi import FastAPI, Depends, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session
from sqlalchemy import func
from pydantic import BaseModel, EmailStr
from typing import Optional, List
from datetime import datetime, timedelta

from models import get_db, User, Review, PlatformConnection
from auth import hash_password, verify_password, create_access_token, get_current_user
from google_api import get_google_auth_url, exchange_code_for_tokens, get_reviews, reply_to_review, parse_google_review
from stripe_handler import create_checkout_session, create_portal_session, handle_webhook_event
from notifications import notify_new_review

app = FastAPI(title="ReviewRadar API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://reviewradar-app.netlify.app",
        "http://localhost:3000",
        "http://localhost:8080",
        "http://127.0.0.1:3000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ─── Pydantic Schemas ───────────────────────────────────────────

class RegisterRequest(BaseModel):
    email: EmailStr
    password: str
    business_name: str

class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"

class ReviewResponse(BaseModel):
    id: int
    platform: str
    author_name: Optional[str]
    rating: Optional[float]
    text: Optional[str]
    reply: Optional[str]
    review_date: Optional[str]

class ReplyRequest(BaseModel):
    text: str

class StatsResponse(BaseModel):
    total_reviews: int
    average_rating: float
    reviews_this_month: int
    platforms_connected: int
    rating_distribution: dict
    monthly_trend: list

class UserProfile(BaseModel):
    id: int
    email: str
    business_name: str
    plan: str
    email_notifications: bool
    created_at: str

class UpdateProfileRequest(BaseModel):
    business_name: Optional[str] = None
    email_notifications: Optional[bool] = None


# ─── Auth Routes ─────────────────────────────────────────────────

@app.post("/api/auth/register", response_model=TokenResponse)
def register(req: RegisterRequest, db: Session = Depends(get_db)):
    existing = db.query(User).filter(User.email == req.email).first()
    if existing:
        raise HTTPException(status_code=400, detail="Dit e-mailadres is al geregistreerd")

    user = User(
        email=req.email,
        hashed_password=hash_password(req.password),
        business_name=req.business_name,
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    token = create_access_token({"sub": user.id})
    return TokenResponse(access_token=token)


@app.post("/api/auth/login", response_model=TokenResponse)
def login(form_data: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == form_data.username).first()
    if not user or not verify_password(form_data.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Onjuist e-mailadres of wachtwoord")

    token = create_access_token({"sub": user.id})
    return TokenResponse(access_token=token)


# ─── User Profile ───────────────────────────────────────────────

@app.get("/api/me", response_model=UserProfile)
def get_profile(user: User = Depends(get_current_user)):
    return UserProfile(
        id=user.id,
        email=user.email,
        business_name=user.business_name,
        plan=user.plan,
        email_notifications=user.email_notifications,
        created_at=user.created_at.isoformat(),
    )


@app.patch("/api/me")
def update_profile(req: UpdateProfileRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if req.business_name is not None:
        user.business_name = req.business_name
    if req.email_notifications is not None:
        user.email_notifications = req.email_notifications
    db.commit()
    return {"status": "ok"}


# ─── Reviews ─────────────────────────────────────────────────────

@app.get("/api/reviews", response_model=List[ReviewResponse])
def list_reviews(
    platform: Optional[str] = None,
    rating: Optional[int] = None,
    limit: int = 50,
    offset: int = 0,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    query = db.query(Review).filter(Review.user_id == user.id)
    if platform:
        query = query.filter(Review.platform == platform)
    if rating:
        query = query.filter(Review.rating == rating)
    reviews = query.order_by(Review.review_date.desc()).offset(offset).limit(limit).all()

    return [
        ReviewResponse(
            id=r.id,
            platform=r.platform,
            author_name=r.author_name,
            rating=r.rating,
            text=r.text,
            reply=r.reply,
            review_date=r.review_date.isoformat() if r.review_date else None,
        )
        for r in reviews
    ]


@app.post("/api/reviews/{review_id}/reply")
async def reply_review(review_id: int, req: ReplyRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if user.plan == "free":
        raise HTTPException(status_code=403, detail="Upgrade naar Starter of Pro om reviews te beantwoorden")

    review = db.query(Review).filter(Review.id == review_id, Review.user_id == user.id).first()
    if not review:
        raise HTTPException(status_code=404, detail="Review niet gevonden")

    # If Google review, send reply via API
    if review.platform == "google" and review.external_id:
        conn = db.query(PlatformConnection).filter(
            PlatformConnection.user_id == user.id,
            PlatformConnection.platform == "google"
        ).first()
        if conn and conn.access_token:
            try:
                await reply_to_review(conn.access_token, review.external_id, req.text)
            except Exception as e:
                raise HTTPException(status_code=500, detail=f"Kon niet reageren via Google: {str(e)}")

    review.reply = req.text
    review.replied_at = datetime.utcnow()
    db.commit()
    return {"status": "ok"}


# ─── Stats ───────────────────────────────────────────────────────

@app.get("/api/stats", response_model=StatsResponse)
def get_stats(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    reviews = db.query(Review).filter(Review.user_id == user.id).all()
    connections = db.query(PlatformConnection).filter(PlatformConnection.user_id == user.id).count()

    now = datetime.utcnow()
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    total = len(reviews)
    avg = sum(r.rating or 0 for r in reviews) / total if total > 0 else 0
    this_month = sum(1 for r in reviews if r.review_date and r.review_date >= month_start)

    # Rating distribution
    dist = {1: 0, 2: 0, 3: 0, 4: 0, 5: 0}
    for r in reviews:
        if r.rating:
            dist[int(r.rating)] = dist.get(int(r.rating), 0) + 1

    # Monthly trend (last 6 months)
    trend = []
    for i in range(5, -1, -1):
        month = now.month - i
        year = now.year
        while month <= 0:
            month += 12
            year -= 1
        m_start = datetime(year, month, 1)
        if month == 12:
            m_end = datetime(year + 1, 1, 1)
        else:
            m_end = datetime(year, month + 1, 1)
        month_reviews = [r for r in reviews if r.review_date and m_start <= r.review_date < m_end]
        month_avg = sum(r.rating or 0 for r in month_reviews) / len(month_reviews) if month_reviews else 0
        trend.append({
            "month": m_start.strftime("%b %Y"),
            "count": len(month_reviews),
            "average": round(month_avg, 1),
        })

    return StatsResponse(
        total_reviews=total,
        average_rating=round(avg, 1),
        reviews_this_month=this_month,
        platforms_connected=connections,
        rating_distribution=dist,
        monthly_trend=trend,
    )


# ─── Google OAuth ────────────────────────────────────────────────

@app.get("/api/google/connect")
def google_connect(user: User = Depends(get_current_user)):
    url = get_google_auth_url(state=str(user.id))
    return {"url": url}


@app.get("/api/google/callback")
async def google_callback(code: str, state: str, db: Session = Depends(get_db)):
    user_id = int(state)
    tokens = await exchange_code_for_tokens(code)

    # Save connection
    conn = db.query(PlatformConnection).filter(
        PlatformConnection.user_id == user_id,
        PlatformConnection.platform == "google"
    ).first()

    if conn:
        conn.access_token = tokens["access_token"]
        conn.refresh_token = tokens.get("refresh_token", conn.refresh_token)
    else:
        conn = PlatformConnection(
            user_id=user_id,
            platform="google",
            access_token=tokens["access_token"],
            refresh_token=tokens.get("refresh_token"),
        )
        db.add(conn)

    db.commit()
    # Redirect to dashboard
    return {"status": "connected", "redirect": "/dashboard.html?connected=google"}


@app.post("/api/google/sync")
async def sync_google_reviews(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    conn = db.query(PlatformConnection).filter(
        PlatformConnection.user_id == user.id,
        PlatformConnection.platform == "google"
    ).first()

    if not conn:
        raise HTTPException(status_code=400, detail="Google is niet verbonden")

    try:
        raw_reviews = await get_reviews(conn.access_token, conn.account_id or "", "")
    except Exception:
        raise HTTPException(status_code=500, detail="Kon reviews niet ophalen van Google")

    new_count = 0
    for raw in raw_reviews:
        parsed = parse_google_review(raw, user.id)
        existing = db.query(Review).filter(
            Review.user_id == user.id,
            Review.external_id == parsed["external_id"]
        ).first()
        if not existing:
            review = Review(**parsed)
            db.add(review)
            new_count += 1
            # Send notification
            db.flush()
            notify_new_review(user, review)

    db.commit()
    return {"synced": new_count}


# ─── Connections ─────────────────────────────────────────────────

@app.get("/api/connections")
def list_connections(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    conns = db.query(PlatformConnection).filter(PlatformConnection.user_id == user.id).all()
    return [
        {
            "id": c.id,
            "platform": c.platform,
            "account_name": c.account_name,
            "connected_at": c.connected_at.isoformat(),
        }
        for c in conns
    ]


@app.delete("/api/connections/{conn_id}")
def delete_connection(conn_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    conn = db.query(PlatformConnection).filter(
        PlatformConnection.id == conn_id,
        PlatformConnection.user_id == user.id
    ).first()
    if not conn:
        raise HTTPException(status_code=404, detail="Verbinding niet gevonden")
    db.delete(conn)
    db.commit()
    return {"status": "ok"}


# ─── Stripe ──────────────────────────────────────────────────────

@app.post("/api/billing/checkout")
def billing_checkout(plan: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    try:
        url = create_checkout_session(user, plan)
        # Save customer ID if newly created
        db.commit()
        return {"url": url}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/billing/portal")
def billing_portal(user: User = Depends(get_current_user)):
    try:
        url = create_portal_session(user)
        return {"url": url}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/webhooks/stripe")
async def stripe_webhook(request: Request, db: Session = Depends(get_db)):
    payload = await request.body()
    sig = request.headers.get("stripe-signature", "")
    try:
        result = handle_webhook_event(payload, sig, db)
        return result
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid webhook")


# ─── Widget API (public) ────────────────────────────────────────

@app.get("/api/widget/{user_id}")
def widget_reviews(user_id: int, db: Session = Depends(get_db)):
    """Public endpoint for the embeddable widget. Returns top reviews."""
    user = db.query(User).filter(User.id == user_id).first()
    if not user or user.plan != "pro":
        raise HTTPException(status_code=403, detail="Widget is alleen beschikbaar voor Pro gebruikers")

    reviews = (
        db.query(Review)
        .filter(Review.user_id == user_id, Review.rating >= 4)
        .order_by(Review.review_date.desc())
        .limit(10)
        .all()
    )
    return [
        {
            "author": r.author_name,
            "rating": r.rating,
            "text": r.text,
            "platform": r.platform,
            "date": r.review_date.isoformat() if r.review_date else None,
        }
        for r in reviews
    ]


# ─── Demo Data (for testing) ────────────────────────────────────

@app.post("/api/demo/seed")
def seed_demo_data(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Seed demo reviews for testing the dashboard."""
    import random
    platforms = ["google", "facebook", "tripadvisor"]
    names = ["Jan de Vries", "Maria Jansen", "Pieter Bakker", "Fatima El Amrani", "Sophie van Dijk",
             "Ahmed Hassan", "Emma de Groot", "Thomas Visser", "Lisa Mulder", "Mohammed Youssef",
             "Anna Smit", "David Cohen", "Karin Bos", "Yusuf Demir", "Charlotte Brouwer"]
    texts_positive = [
        "Geweldige service! Zeker een aanrader.",
        "Top kwaliteit, ik kom zeker terug.",
        "Heel vriendelijk personeel, fijne ervaring.",
        "Beste in de buurt, altijd consistent goed.",
        "Precies wat ik zocht, heel tevreden!",
        "Snelle service en goede prijs-kwaliteit verhouding.",
    ]
    texts_neutral = [
        "Was oké, niets bijzonders.",
        "Gemiddeld, kan beter maar ook slechter.",
        "Redelijke ervaring, niet slecht.",
    ]
    texts_negative = [
        "Moest lang wachten, dat kan beter.",
        "Niet helemaal wat ik verwachtte.",
    ]

    now = datetime.utcnow()
    for i in range(20):
        rating = random.choices([5, 4, 3, 2, 1], weights=[40, 30, 15, 10, 5])[0]
        if rating >= 4:
            text = random.choice(texts_positive)
        elif rating == 3:
            text = random.choice(texts_neutral)
        else:
            text = random.choice(texts_negative)

        review = Review(
            user_id=user.id,
            platform=random.choice(platforms),
            author_name=random.choice(names),
            rating=rating,
            text=text,
            review_date=now - timedelta(days=random.randint(0, 180)),
        )
        db.add(review)

    db.commit()
    return {"seeded": 20}


# ─── Health Check ────────────────────────────────────────────────

@app.get("/api/health")
def health():
    return {"status": "ok", "service": "ReviewRadar API", "version": "1.0.0"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
