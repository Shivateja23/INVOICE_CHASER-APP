
from datetime import date
from pathlib import Path



import os
from dotenv import load_dotenv
from google import genai
from fastapi import HTTPException

from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import create_engine, Column, Integer, String, Float, Date
from sqlalchemy.orm import declarative_base, sessionmaker

load_dotenv()

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

gemini_client = (
    genai.Client(api_key=GEMINI_API_KEY)
    if GEMINI_API_KEY
    else None
)

BASE_DIR = Path(__file__).resolve().parent
DATABASE_URL = f"sqlite:///{BASE_DIR / 'invoices.db'}"

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False}
)
SessionLocal = sessionmaker(bind=engine)
Base = declarative_base()
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

app = FastAPI(title="Invoice Chaser")


class Invoice(Base):
    __tablename__ = "invoices"

    id = Column(Integer, primary_key=True, index=True)
    client_name = Column(String(120), nullable=False)
    client_email = Column(String(200), default="")
    invoice_number = Column(String(80), unique=True, nullable=False)
    amount = Column(Float, nullable=False)
    phone = Column(String, nullable=True)
    due_date = Column(Date, nullable=False)
    status = Column(String(20), default="Unpaid")


Base.metadata.create_all(bind=engine)


@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    db = SessionLocal()
    try:
        invoices = db.query(Invoice).order_by(Invoice.due_date).all()
        today = date.today()

        for invoice in invoices:
            invoice.is_overdue = (
                invoice.status != "Paid" and invoice.due_date < today
            )

        total_unpaid = sum(
            inv.amount for inv in invoices if inv.status != "Paid"
        )
        total_overdue = sum(
            inv.amount for inv in invoices
            if inv.status != "Paid" and inv.due_date < today
        )

        return templates.TemplateResponse(
            request=request,
            name="index.html",
            context={
                "invoices": invoices,
                "total_unpaid": total_unpaid,
                "total_overdue": total_overdue,
                "today": today,
            },
        )
    finally:
        db.close()


@app.post("/add")
def add_invoice(
    client_name: str = Form(...),
    client_email: str = Form(""),
    phone: str = Form(...),
    invoice_number: str = Form(...),
    amount: float = Form(...),
    due_date: date = Form(...),
):
    db = SessionLocal()
    try:
        invoice = Invoice(
            client_name=client_name.strip(),
            client_email=client_email.strip(),
            phone=phone,
            invoice_number=invoice_number.strip(),
            amount=amount,
            due_date=due_date,
            status="Unpaid",
        )
        db.add(invoice)
        db.commit()
    except Exception:
        db.rollback()
        return HTMLResponse(
            "Could not add invoice. Check that the invoice number is unique "
            "and all values are valid.",
            status_code=400,
        )
    finally:
        db.close()

    return RedirectResponse("/", status_code=303)


@app.post("/mark-paid/{invoice_id}")
def mark_paid(invoice_id: int):
    db = SessionLocal()
    try:
        invoice = db.query(Invoice).filter(Invoice.id == invoice_id).first()
        if invoice:
            invoice.status = "Paid"
            db.commit()
    finally:
        db.close()

    return RedirectResponse("/", status_code=303)


@app.get("/health")
def health():
    return {"status": "ok", "app": "Invoice Chaser"}



@app.post("/generate-reminder/{invoice_id}")
def generate_reminder(invoice_id: int):
   
    db = SessionLocal()
    try:
        invoice = db.query(Invoice).filter(Invoice.id == invoice_id).first()
        if invoice is None:
            raise HTTPException(status_code=404, detail="Invoice not found.")
        if invoice.status == "Paid":
            raise HTTPException(status_code=400, detail="This invoice is already paid.")
        
        today = date.today()
        days_overdue = (today - invoice.due_date).days
        if days_overdue > 0:
            timing = f"{days_overdue} days overdue"
        elif days_overdue == 0:
            timing = "due today"
        else:
            timing = f"due in {abs(days_overdue)} days"

        prompt = (
            f"Write a single, short, and polite WhatsApp reminder message "
            f"for a freelancer to send to client {invoice.client_name} "
            f"regarding invoice #{invoice.invoice_number} for amount ₹{invoice.amount}. "
            f"The payment is {timing}. "
            f"""CRITICAL: Output ONLY the message text. Do not include options, bullet points, or markdown formatting like bolding or headers. Also consider it they are 
             in due mention the due date in the message and if they are overdue mention how many days overdue and in brackt mention months too. If there due date is near mention how many days they have left to pay. The message should be polite, professional, and concise."""
        )
 

        response = gemini_client.models.generate_content(
            model="gemini-3.5-flash-lite",
            contents=prompt
        )
        reminder_text = response.text   

        return {
            "invoice_id": invoice.id,
            "client_name": invoice.client_name,
            "invoice_number": invoice.invoice_number,
            "phone": invoice.phone,
            "reminder": reminder_text,
            "channel": "WhatsApp"
        }
    except Exception as e:
        # This will return the exact Python error message to your browser screen!
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()


@app.post("/delete-invoice/{invoice_id}")
def delete_invoice(invoice_id: int):
    db = SessionLocal()
    try:
        invoice = db.query(Invoice).filter(Invoice.id == invoice_id).first()
        if invoice:
            db.delete(invoice)
            db.commit()
        return RedirectResponse(url="/", status_code=303)
    finally:
        db.close()
