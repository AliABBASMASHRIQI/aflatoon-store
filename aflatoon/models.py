from datetime import date

from werkzeug.security import generate_password_hash, check_password_hash

from aflatoon.extensions import db

# ---------------------------------------------------------------------------
# Dropdown list values (mirror of the LISTS sheet)
# ---------------------------------------------------------------------------
CATEGORIES = ["Shirts", "T-Shirts", "Jeans", "Trousers", "Jackets", "Kurtas",
              "Dresses", "Shoes", "Accessories", "Bags & Luggage", "Soft Toys",
              "Vintage / Collectibles", "Home Decor", "Other Clothing",
              "Non-Clothing"]
ITEM_TYPES = ["Clothing", "Non-Clothing"]
BRAND_TYPES = ["Thrifted Brand", "Homegrown Brand", "Unbranded", "Vintage/Unknown", "Other"]
PAYMENT_METHODS = ["Cash", "UPI", "Card", "Credit/Outstanding", "Other"]
EXPENSE_CATEGORIES = ["Rent", "Salary", "Electricity", "Packaging", "Repairs", "Transportation",
                      "Tea/Food", "Marketing", "Ads", "Trash/Cleaning", "Stationery",
                      "Transport/Delivery", "Storage", "Other"]
EXPENSE_NATURES = ["Fixed", "Variable"]
ITEM_STATUSES = ["Unprocessed", "Processing", "Ready for Sale", "Reserved", "Sold",
                 "Returned", "Damaged", "Lost"]
ADJUSTMENT_TYPES = ["Physical Count", "Damaged", "Lost", "Found", "Correction"]
SUPPLIER_TYPES = ["Wholesaler", "Brand", "Manufacturer", "Thrift Vendor", "Other Vendor"]
LOAN_TYPES = ["Bank Loan", "Gold Loan", "Business Loan", "Borrowed from Person",
              "Credit Card", "Shop Equipment", "Other"]
TXN_TYPES = ["Sale", "Refund", "Other Income", "Expense", "Purchase", "EMI",
             "Owner Investment", "Owner Withdrawal", "Transfer",
             "Major Reserve Spend"]

# Payment methods that actually move money. "Credit/Outstanding" and "Other"
# do not, so they never auto-post to the cash ledger.
CASH_METHODS = ("Cash", "UPI", "Card")

# (field_name, label) for the 12 monthly budget categories
BUDGET_CATEGORY_FIELDS = [
    ("rent", "Rent"),
    ("salary", "Salary"),
    ("electricity", "Electricity"),
    ("packaging", "Packaging"),
    ("repairs", "Repairs"),
    ("transportation", "Transportation"),
    ("tea_food", "Tea/Food"),
    ("marketing", "Marketing"),
    ("ads", "Ads"),
    ("stationery", "Stationery"),
    ("trash_cleaning", "Trash/Cleaning"),
    ("other", "Other"),
]


class Settings(db.Model):
    """Single-row application settings (mirror of CONTROL PANEL + a few extras)."""
    __tablename__ = "settings"

    id = db.Column(db.Integer, primary_key=True)
    store_name = db.Column(db.String(120), default="Aflatoon Studio")
    store_type = db.Column(db.String(120), default="Women + Men, thrift-led mixed retail")
    currency = db.Column(db.String(8), default="INR")

    planning_sales_bad = db.Column(db.Numeric(12, 2), default=50000)
    planning_sales_normal = db.Column(db.Numeric(12, 2), default=100000)
    planning_sales_good = db.Column(db.Numeric(12, 2), default=150000)

    initial_stock_estimate = db.Column(db.Integer, default=0)
    est_new_items_per_month = db.Column(db.Integer, default=175)

    restock_pct = db.Column(db.Numeric(6, 4), default=0.10)
    major_restock_pct = db.Column(db.Numeric(6, 4), default=0.20)
    owner_cash_target_pct = db.Column(db.Numeric(6, 4), default=0.10)

    critical_coverage_days = db.Column(db.Integer, default=14)
    low_coverage_days = db.Column(db.Integer, default=30)
    slow_moving_days = db.Column(db.Integer, default=60)
    dead_stock_days = db.Column(db.Integer, default=90)
    emi_alert_days = db.Column(db.Integer, default=5)

    starting_cash = db.Column(db.Numeric(12, 2), default=0)
    starting_bank_upi = db.Column(db.Numeric(12, 2), default=0)

    opening_payables = db.Column(db.Numeric(12, 2), default=0)
    opening_date = db.Column(db.Date)
    opening_done = db.Column(db.Boolean, default=False)
    last_reconcile = db.Column(db.Text)

    admin_user = db.Column(db.String(60), default="admin")
    admin_pass_hash = db.Column(db.String(255), nullable=True)

    created_at = db.Column(db.DateTime, default=db.func.now())

    @property
    def monthly_target(self):
        return self.planning_sales_good or 0

    def check_password(self, password: str) -> bool:
        if self.admin_pass_hash:
            return check_password_hash(self.admin_pass_hash, password)
        # no hash -> compare against plain env default
        from flask import current_app
        return password == current_app.config.get("ADMIN_PASSWORD")

    def set_password(self, password: str):
        self.admin_pass_hash = generate_password_hash(password)


class Supplier(db.Model):
    __tablename__ = "suppliers"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    supplier_type = db.Column(db.String(50), default=SUPPLIER_TYPES[0])
    contact_name = db.Column(db.String(120))
    phone = db.Column(db.String(30))
    email = db.Column(db.String(120))
    address = db.Column(db.Text)
    payment_terms = db.Column(db.String(120))
    is_active = db.Column(db.Boolean, default=True)
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=db.func.now())

    batches = db.relationship("PurchaseBatch", back_populates="supplier")
    items = db.relationship("Item", back_populates="supplier")

    @property
    def total_purchased(self):
        total = db.session.query(db.func.coalesce(db.func.sum(PurchaseBatch.total_cost), 0)) \
            .filter(PurchaseBatch.supplier_id == self.id).scalar()
        return total or 0

    @property
    def outstanding(self):
        total = db.session.query(db.func.coalesce(db.func.sum(
            PurchaseBatch.total_cost - PurchaseBatch.paid_amount), 0)) \
            .filter(PurchaseBatch.supplier_id == self.id).scalar()
        return max(total or 0, 0)


class PurchaseBatch(db.Model):
    __tablename__ = "purchase_batches"

    id = db.Column(db.Integer, primary_key=True)
    batch_id = db.Column(db.String(20), unique=True, nullable=False)
    purchase_date = db.Column(db.Date, nullable=False, default=date.today)
    supplier_id = db.Column(db.Integer, db.ForeignKey("suppliers.id"), nullable=True)
    supplier_type = db.Column(db.String(50), default=SUPPLIER_TYPES[0])
    invoice_ref = db.Column(db.String(120))
    qty_purchased = db.Column(db.Integer, default=0)
    total_cost = db.Column(db.Numeric(12, 2), default=0)
    paid_amount = db.Column(db.Numeric(12, 2), default=0)
    notes = db.Column(db.Text)

    supplier = db.relationship("Supplier", back_populates="batches")
    items = db.relationship("Item", back_populates="purchase_batch")

    @property
    def outstanding(self):
        return max((self.total_cost or 0) - (self.paid_amount or 0), 0)

    @property
    def cost_per_item(self):
        try:
            return (self.total_cost or 0) / self.qty_purchased if self.qty_purchased else 0
        except ZeroDivisionError:
            return 0

    @property
    def processed_qty(self):
        return len(self.items)

    @property
    def unprocessed_qty(self):
        return max((self.qty_purchased or 0) - self.processed_qty, 0)


class Item(db.Model):
    __tablename__ = "items"

    id = db.Column(db.Integer, primary_key=True)
    item_id = db.Column(db.String(20), unique=True, nullable=False)
    purchase_batch_id = db.Column(db.Integer, db.ForeignKey("purchase_batches.id"), nullable=True)
    supplier_id = db.Column(db.Integer, db.ForeignKey("suppliers.id"), nullable=True)
    item_type = db.Column(db.String(30), default=ITEM_TYPES[0])
    category = db.Column(db.String(50))
    subcategory = db.Column(db.String(80))
    brand_type = db.Column(db.String(50), default=BRAND_TYPES[0])
    brand_name = db.Column(db.String(120))
    description = db.Column(db.Text)
    size = db.Column(db.String(40))
    colour = db.Column(db.String(40))
    purchase_date = db.Column(db.Date)
    allocated_cost = db.Column(db.Numeric(12, 2), default=0)
    listed_price = db.Column(db.Numeric(12, 2), default=0)
    mrp = db.Column(db.Numeric(12, 2), default=0)
    current_status = db.Column(db.String(30), default=ITEM_STATUSES[0])
    date_ready = db.Column(db.Date)
    date_sold = db.Column(db.Date)
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=db.func.now())

    purchase_batch = db.relationship("PurchaseBatch", back_populates="items")
    supplier = db.relationship("Supplier", back_populates="items")
    sales = db.relationship("Sale", back_populates="item")
    adjustments = db.relationship("StockAdjustment", back_populates="item")


class Sale(db.Model):
    __tablename__ = "sales"

    id = db.Column(db.Integer, primary_key=True)
    sale_date = db.Column(db.Date, nullable=False, default=date.today)
    bill_id = db.Column(db.String(40))
    item_id = db.Column(db.Integer, db.ForeignKey("items.id"), nullable=False)
    qty = db.Column(db.Integer, default=1)
    listed_price = db.Column(db.Numeric(12, 2), default=0)
    discount = db.Column(db.Numeric(12, 2), default=0)
    payment_method = db.Column(db.String(30), default=PAYMENT_METHODS[0])
    is_returned = db.Column(db.Boolean, default=False)
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=db.func.now())

    item = db.relationship("Item", back_populates="sales")

    @property
    def final_value(self):
        return max((self.qty or 0) * (self.listed_price or 0) - (self.discount or 0), 0)

    @property
    def category(self):
        return self.item.category if self.item else ""

    @property
    def item_type(self):
        return self.item.item_type if self.item else ""

    @property
    def cost_total(self):
        if self.item and self.item.allocated_cost is not None:
            return self.item.allocated_cost * (self.qty or 1)
        return None

    @property
    def gross_profit(self):
        if self.cost_total is None:
            return None
        return self.final_value - self.cost_total

    @property
    def gross_margin(self):
        if self.gross_profit is None or not self.final_value:
            return 0
        return self.gross_profit / self.final_value


class Expense(db.Model):
    __tablename__ = "expenses"

    id = db.Column(db.Integer, primary_key=True)
    expense_date = db.Column(db.Date, nullable=False, default=date.today)
    expense_id = db.Column(db.String(20), unique=True, nullable=False)
    category = db.Column(db.String(50))
    nature = db.Column(db.String(20), default=EXPENSE_NATURES[0])
    description = db.Column(db.Text)
    amount = db.Column(db.Numeric(12, 2), default=0)
    payment_method = db.Column(db.String(30), default=PAYMENT_METHODS[0])
    is_paid = db.Column(db.Boolean, default=True)
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=db.func.now())


class StockAdjustment(db.Model):
    __tablename__ = "stock_adjustments"

    id = db.Column(db.Integer, primary_key=True)
    adjustment_date = db.Column(db.Date, nullable=False, default=date.today)
    adjustment_id = db.Column(db.String(20), unique=True, nullable=False)
    item_id = db.Column(db.Integer, db.ForeignKey("items.id"), nullable=False)
    adjustment_type = db.Column(db.String(30))
    qty_change = db.Column(db.Integer, default=0)
    reason = db.Column(db.Text)
    approved_by = db.Column(db.String(120))
    is_processed = db.Column(db.Boolean, default=False)
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=db.func.now())

    item = db.relationship("Item", back_populates="adjustments")


class EmiTracker(db.Model):
    __tablename__ = "emi_tracker"

    id = db.Column(db.Integer, primary_key=True)
    loan_id = db.Column(db.String(20), unique=True, nullable=False)
    loan_name = db.Column(db.String(120))
    lender = db.Column(db.String(120))
    loan_type = db.Column(db.String(60), default=LOAN_TYPES[0])
    emi_amount = db.Column(db.Numeric(12, 2), default=0)
    due_day = db.Column(db.Integer)
    next_due_date = db.Column(db.Date)
    outstanding_principal = db.Column(db.Numeric(12, 2), default=0)
    remaining_emis = db.Column(db.Integer, default=0)
    is_paid = db.Column(db.Boolean, default=False)
    paid_date = db.Column(db.Date)
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=db.func.now())

    @property
    def days_to_due(self):
        if not self.next_due_date:
            return None
        return (self.next_due_date - date.today()).days

    @property
    def alert(self):
        if self.is_paid:
            return "PAID"
        if self.days_to_due is None:
            return ""
        s = Settings.query.first()
        threshold = s.emi_alert_days if s else 5
        if self.days_to_due <= threshold:
            return "DUE SOON"
        return "UPCOMING"


class CashTxn(db.Model):
    __tablename__ = "cash_txns"

    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.Date, nullable=False, default=date.today)
    txn_id = db.Column(db.String(20), unique=True, nullable=False)
    txn_type = db.Column(db.String(40), default=TXN_TYPES[0])
    description = db.Column(db.Text)
    cash_in = db.Column(db.Numeric(12, 2), default=0)
    cash_out = db.Column(db.Numeric(12, 2), default=0)
    bank_upi_in = db.Column(db.Numeric(12, 2), default=0)
    bank_upi_out = db.Column(db.Numeric(12, 2), default=0)
    reference = db.Column(db.String(120))
    notes = db.Column(db.Text)
    source_type = db.Column(db.String(20))
    source_id = db.Column(db.Integer)
    created_at = db.Column(db.DateTime, default=db.func.now())


class MonthlyBudget(db.Model):
    __tablename__ = "monthly_budgets"

    id = db.Column(db.Integer, primary_key=True)
    month = db.Column(db.Date, unique=True, nullable=False)  # first day of month

    for fname, _label in BUDGET_CATEGORY_FIELDS:
        locals()[fname] = db.Column(db.Numeric(12, 2), default=0)

    @property
    def total_budget(self):
        return sum((getattr(self, f) or 0) for f, _ in BUDGET_CATEGORY_FIELDS)

    @property
    def actual(self):
        start, end = month_start_end_v(self.month)
        total = db.session.query(db.func.coalesce(db.func.sum(Expense.amount), 0)) \
            .filter(Expense.expense_date >= start, Expense.expense_date < end).scalar()
        return total or 0

    @property
    def remaining(self):
        return self.total_budget - self.actual

    @property
    def status(self):
        return "OVER BUDGET" if self.remaining < 0 else "OK"


def month_start_end_v(d: date):
    from aflatoon.helpers import month_start_end
    return month_start_end(d)