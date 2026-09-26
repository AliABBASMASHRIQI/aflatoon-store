"""Dev-only smoke test: walks every GET route and reports failures.

Usage:  .venv\\Scripts\\python.exe smoke_test.py
Not part of the application - delete it before deploying if you like.
"""
import os
import re
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from aflatoon import create_app
from aflatoon.extensions import db

app = create_app()
app.config['TESTING']=True
app.config['PROPAGATE_EXCEPTIONS']=True

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
    return 1 if (bad or cfail) else 0


if __name__ == "__main__":
    sys.exit(main())
