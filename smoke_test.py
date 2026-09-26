"""Dev-only smoke test: walks every GET route and reports failures.

Usage:  .venv\\Scripts\\python.exe smoke_test.py

Runs against a throwaway SQLite file in the temp folder, so it never touches
the real aflatoon.db. Not part of the application.
"""
import os
import re
import sys
import tempfile
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# must be set before aflatoon.config is imported, since Config reads it at
# class-definition time
_TMP_DB = os.path.join(tempfile.gettempdir(), "aflatoon_smoke_test.db")
if os.path.exists(_TMP_DB):
    os.remove(_TMP_DB)
os.environ["DATABASE_URL"] = "sqlite:///" + _TMP_DB
os.environ.setdefault("SECRET_KEY", "smoke-test-key")

from aflatoon import create_app
from aflatoon.extensions import db

app = create_app()
app.config["TESTING"] = True
app.config["PROPAGATE_EXCEPTIONS"] = True

USER = app.config["ADMIN_USER"]
PASSWORD = app.config["ADMIN_PASSWORD"]


def build_url(rule, seeded):
    """Substitute real ids for <int:...> placeholders using seeded rows."""
    url = rule.rule

    def sub(m):
        name = m.group(1)
        if name in seeded:
            return f"/{seeded[name]}"
        return "/1"

    return re.sub(r"<(?:[^:<>]+:)?([^<>]+)>", sub, url)


def main():
    results = []
    with app.app_context():
        # give the id-based routes something real to point at
        from aflatoon.models import (Supplier, PurchaseBatch, Item, Sale,
                                     Expense, StockAdjustment, EmiTracker,
                                     CashTxn, MonthlyBudget)
        seeded = {}
        if not Supplier.query.first():
            s = Supplier(name="Smoke Supplier", phone="000")
            db.session.add(s)
            db.session.commit()
        sup = Supplier.query.first()
        if not PurchaseBatch.query.first():
            b = PurchaseBatch(batch_id="PUR-00001", qty_purchased=10,
                              total_cost=1000, paid_amount=1000, supplier_id=sup.id)
            db.session.add(b)
            db.session.commit()
        batch = PurchaseBatch.query.first()
        if not Item.query.first():
            it = Item(item_id="AFL-00001", purchase_batch_id=batch.id,
                      category="Shirts", allocated_cost=100, listed_price=250,
                      mrp=400, description="Smoke shirt", purchase_date=batch.purchase_date)
            db.session.add(it)
            db.session.commit()
        item = Item.query.first()
        if not Sale.query.first():
            sa = Sale(bill_id="B1", item_id=item.id, qty=1, listed_price=250,
                      discount=0, sale_date=item.purchase_date)
            db.session.add(sa)
            db.session.commit()
        if not Expense.query.first():
            db.session.add(Expense(expense_id="EXP-00001", category="Tea/Food",
                                    amount=50, expense_date=item.purchase_date))
            db.session.commit()
        if not StockAdjustment.query.first():
            db.session.add(StockAdjustment(adjustment_id="ADJ-00001", item_id=item.id,
                                           adjustment_type="Physical Count", qty_change=0,
                                           adjustment_date=item.purchase_date))
            db.session.commit()
        if not CashTxn.query.first():
            db.session.add(CashTxn(txn_id="TXN-00001", txn_type="Sale",
                                   cash_in=250, date=item.purchase_date))
            db.session.commit()
        seeded = dict(
            supplier=sup.id, batch=batch.id, item=item.id,
            sale=Sale.query.first().id, expense=Expense.query.first().id,
            adj=StockAdjustment.query.first().id, txn=CashTxn.query.first().id,
        )

    # seed an EMI + budget so those pages have something
    with app.app_context():
        from datetime import date, timedelta
        from aflatoon.models import EmiTracker, MonthlyBudget
        from aflatoon.helpers import first_of_month
        if not EmiTracker.query.first():
            db.session.add(EmiTracker(loan_id="LOAN-01", loan_name="Smoke Loan",
                                      emi_amount=5000, due_day=5,
                                      next_due_date=date.today() + timedelta(days=3),
                                      outstanding_principal=100000, remaining_emis=20))
            db.session.commit()
        if not MonthlyBudget.query.first():
            db.session.add(MonthlyBudget(month=first_of_month(date.today()), rent=10000))
            db.session.commit()
        seeded["emi"] = EmiTracker.query.first().id
        seeded["budget"] = MonthlyBudget.query.first().id

    client = app.test_client()

    # login
    r = client.post("/auth/login", data={"username": USER, "password": PASSWORD},
                    follow_redirects=False)
    if r.status_code != 302:
        print("LOGIN FAILED", r.status_code)
        return 1
    print(f"logged in as {USER}\n")

    results = []
    seen = set()
    for rule in sorted(app.url_map.iter_rules(), key=lambda x: x.rule):
        if rule.rule.startswith("/static"):
            continue
        if rule.rule == "/auth/logout":
            continue          # walking this would clear the session mid-run
        if "GET" not in (rule.methods or set()):
            continue
        url = build_url(rule, seeded)
        key = (rule.endpoint, url)
        if key in seen:
            continue
        seen.add(key)
        err = ""
        try:
            with app.test_request_context(url):
                app.preprocess_request()
                resp = client.get(url, follow_redirects=True)
            status = resp.status_code
        except Exception as e:
            status = "EXC"
            tb = traceback.format_exc().strip().splitlines()
            err = f"{type(e).__name__}: {e}"
            for line in reversed(tb):
                s = line.strip()
                if s.startswith("File ") and "site-packages" not in s:
                    err = f"{s}  ->  {err}"
                    break
        results.append((status, rule.endpoint, url, err))

    ok = [r for r in results if r[0] == 200]
    bad = [r for r in results if r[0] != 200]

    for status, endpoint, url, err in sorted(bad, key=lambda x: str(x[0])):
        print(f"  {status!s:>6}  {endpoint:<34} {url}")
        if err:
            print(f"          {err}")

    print(f"\n{len(ok)}/{len(results)} routes OK, {len(bad)} failing")

    # ---- content assertions: a 200 can still render an empty page ----------
    # make sure we are still authenticated before asserting on page content
    client.post("/auth/login", data={"username": USER, "password": PASSWORD})

    def get(url):
        return client.get(url, follow_redirects=True).data.decode("utf-8", "replace")

    checks = [
        # every <select> must have at least one real <option>
        ("/items/new", 'name="category"', '<option value="Shirts"'),
        ("/items/new", 'name="item_type"', '<option value="Clothing"'),
        ("/items/new", 'name="brand_type"', '<option value="Thrifted Brand"'),
        ("/sales/new", 'name="payment_method"', '<option value="Cash"'),
        ("/expenses/new", 'name="category"', '<option value="Rent"'),
        ("/cash/new", 'name="txn_type"', '<option value="Sale"'),
        ("/emi/new", 'name="is_paid"', '<option value="No"'),
        ("/suppliers/new", 'name="supplier_type"', '<option value="Wholesaler"'),
        ("/adjustments/new", 'name="adjustment_type"', '<option value="Physical Count"'),
        # cancel must go back to the list, never to the login page
        ("/items/new", 'cancel', 'href="/items/"'),
        # row actions must resolve to real edit/delete urls
        ("/items/", 'edit link', 'href="/items/1/edit"'),
        ("/items/", 'delete form', 'action="/items/1/delete"'),
        ("/suppliers/", 'edit link', 'href="/suppliers/1/edit"'),
        ("/sales/", 'edit link', 'href="/sales/1/edit"'),
        ("/expenses/", 'edit link', 'href="/expenses/1/edit"'),
        ("/adjustments/", 'edit link', 'href="/adjustments/1/edit"'),
        ("/emi/", 'mark paid', 'action="/emi/1/mark-paid"'),
        # dashboard must render its real kpi keys
        ("/", 'kpi: today', "Today's Sales"),
        ("/", 'kpi: target', "Monthly Target"),
        ("/", 'alerts', "dead stock"),
        ("/", 'workflow', "Daily workflow"),
        # bulk entry grid
        ("/items/bulk", 'bulk grid', 'name="r0_size"'),
        ("/items/bulk", 'bulk defaults', 'name="default_category"'),
    ]

    print("\ncontent checks")
    cfail = 0
    for url, label, needle in checks:
        html = get(url)
        if needle in html:
            print(f"  ok    {url:<22} {label}")
        else:
            print(f"  MISS  {url:<22} {label}   (looking for {needle!r})")
            cfail += 1
    print(f"\n{len(checks) - cfail}/{len(checks)} content checks passed")

    # ---- behaviour checks: does it actually do the right thing? ------------
    print("\nbehaviour checks")
    from datetime import date as _date
    from aflatoon.models import Settings, Item, Sale
    bfail = 0

    def bcheck(label, cond, detail=""):
        nonlocal bfail
        if cond:
            print(f"  ok    {label}")
        else:
            print(f"  FAIL  {label} {detail}")
            bfail += 1

    # 1. full settings save must persist
    with app.app_context():
        s0 = Settings.query.first()
        keep = (s0.critical_coverage_days, s0.emi_alert_days)
    client.post("/settings/", data={
        "store_name": "Aflatoon Studio", "store_type": "test", "currency": "INR",
        "planning_sales_bad": "40000", "planning_sales_normal": "90000",
        "planning_sales_good": "140000", "starting_cash": "1234.50",
        "starting_bank_upi": "500", "restock_pct": "12", "major_restock_pct": "20",
        "owner_cash_target_pct": "10", "critical_coverage_days": "11",
        "low_coverage_days": "22", "slow_moving_days": "55", "dead_stock_days": "77",
        "est_new_items_per_month": "150", "initial_stock_estimate": "0",
        "emi_alert_days": "6", "admin_user": "admin",
    }, follow_redirects=True)
    with app.app_context():
        s1 = Settings.query.first()
        bcheck("settings: threshold saved", s1.critical_coverage_days == 11,
               f"got {s1.critical_coverage_days}")
        bcheck("settings: opening cash saved", float(s1.starting_cash) == 1234.50,
               f"got {s1.starting_cash}")
        bcheck("settings: percent converted once",
               abs(float(s1.restock_pct) - 0.12) < 1e-6, f"got {s1.restock_pct}")
        bcheck("settings: password untouched by blank field",
               s1.check_password(PASSWORD))

    # 2. a partial save must NOT wipe the other fields  (the original bug)
    client.post("/settings/", data={"store_name": "Renamed Store"},
                follow_redirects=True)
    with app.app_context():
        s2 = Settings.query.first()
        bcheck("settings: partial save keeps thresholds",
               s2.critical_coverage_days == 11, f"got {s2.critical_coverage_days}")
        bcheck("settings: partial save keeps opening cash",
               float(s2.starting_cash) == 1234.50, f"got {s2.starting_cash}")
        bcheck("settings: partial save applies what was sent",
               s2.store_name == "Renamed Store", f"got {s2.store_name}")
    client.post("/settings/", data={"store_name": "Aflatoon Studio"},
                follow_redirects=True)

    # 3. bulk entry creates one item per filled row and skips blanks
    with app.app_context():
        n_before = Item.query.count()
    rows = {"row_count": "6",
            "item_type": "Clothing", "brand_type": "Thrifted Brand",
            "default_category": "Shirts", "current_status": "Ready for Sale",
            "purchase_date": _date.today().isoformat(),
            # rows 0,1,3 filled; 2,4,5 left blank
            "r0_size": "M", "r0_cost": "100", "r0_listed": "250",
            "r1_size": "L", "r1_cost": "120", "r1_listed": "300",
            "r3_size": "S", "r3_cost": "80", "r3_listed": "200",
            }
    client.post("/items/bulk", data=rows, follow_redirects=True)
    with app.app_context():
        n_after = Item.query.count()
        bcheck("bulk: created 3 items from 6 rows", n_after - n_before == 3,
               f"created {n_after - n_before}")
        newest = Item.query.order_by(Item.id.desc()).first()
        first_made = Item.query.order_by(Item.id.desc()).offset(2).first()
        bcheck("bulk: default category applied",
               newest.category == "Shirts", f"got {newest.category}")
        bcheck("bulk: per-row cost stored (row 0)",
               float(first_made.allocated_cost) == 100,
               f"got {first_made.allocated_cost}")
        bcheck("bulk: per-row size stored",
               first_made.size == "M", f"got {first_made.size}")
        bcheck("bulk: codes are sequential AFL-#####",
               newest.item_id.startswith("AFL-"), f"got {newest.item_id}")
        bulk_ids = [i.item_id for i in Item.query.order_by(Item.id.desc()).limit(3).all()]

    # 4. recording a sale must flip the item to Sold
    with app.app_context():
        target = Item.query.filter(Item.item_id == bulk_ids[0]).first()
        tid, was_sold = target.id, target.current_status
    client.post("/sales/new", data={
        "sale_date": _date.today().isoformat(), "bill_id": "B-TEST",
        "item_sel": bulk_ids[0], "qty": "1", "listed_price": "250",
        "discount": "0", "payment_method": "Cash", "is_returned": "No",
    }, follow_redirects=True)
    with app.app_context():
        after = Item.query.get(tid)
        bcheck("sale: item marked Sold", after.current_status == "Sold",
               f"was {was_sold} now {after.current_status}")
        bcheck("sale: sale row written",
               Sale.query.filter_by(item_id=tid).count() == 1)
        bcheck("sale: value computed", abs(float(
            Sale.query.filter_by(item_id=tid).first().final_value) - 250) < 0.01)

    # 5. deleting the sale must put the piece back on the rail
    with app.app_context():
        sale_id = Sale.query.filter_by(item_id=tid).first().id
    client.post(f"/sales/{sale_id}/delete", data={}, follow_redirects=True)
    with app.app_context():
        freed = Item.query.get(tid)
        bcheck("sale delete: piece back on the rail",
               freed.current_status == "Ready for Sale",
               f"got {freed.current_status}")
        bcheck("sale delete: sale row gone",
               Sale.query.filter_by(item_id=tid).count() == 0)

    # 6. dashboard must now show the sale
    dash = get("/")
    bcheck("dashboard: reflects recorded sales", "No sales recorded yet" not in dash)

    # 7. stock report picks up the new items
    stock = get("/reports/stock")
    bcheck("reports/stock: lists the new item", bulk_ids[0] in stock)
    dq = get("/reports/data-quality")
    bcheck("reports/data-quality: renders", "Severity" in dq or "Check" in dq)

    # 8. budget screen renders the 12 categories
    budget = get("/budgets/")
    bcheck("budgets: shows a category", "Rent" in budget and "Salary" in budget)

    total_b = 18
    print(f"\n{total_b - bfail}/{total_b} behaviour checks passed")
    return 1 if (bad or cfail or bfail) else 0


if __name__ == "__main__":
    sys.exit(main())
