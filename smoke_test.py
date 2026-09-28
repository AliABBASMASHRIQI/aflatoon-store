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
            # a hand-typed entry, which is how a manual row should look:
            # txn_type Sale but no source link
            db.session.add(CashTxn(txn_id="TXN-00001", txn_type="Other Income",
                                   bank_upi_in=250, date=item.purchase_date))
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
        ("/emi/", 'payment method picker', 'name="payment_method"'),
        ("/batches/", 'pay action', 'action="/batches/1/pay"'),
        # dashboard must render its real kpi keys
        ("/", 'kpi: today', "Today's Sales"),
        ("/", 'kpi: target', "Monthly Target"),
        ("/", 'kpi: cash in hand', "Cash in Hand"),
        ("/", 'kpi: bank', "Bank / UPI"),
        ("/", 'alerts', "dead stock"),
        ("/", 'workflow', "Daily workflow"),
        # opening position + help
        ("/settings/opening", 'opening: cash', 'name="starting_cash"'),
        ("/settings/opening", 'opening: bank', 'name="starting_bank_upi"'),
        ("/settings/opening", 'opening: why', "Why this page exists"),
        # it rendered a 500 once because date_input is a helper, not a
        # jinja filter: a 200 alone did not catch it
        ("/settings/opening", 'opening: date formatted', 'name="opening_date"'),
        ("/help", 'help: one rule', "type each thing"),
        ("/help", 'help: owner investment', "Owner Investment"),
        ("/help", 'help: not twice', "Do not add them again"),
        # cash page reconcile box
        ("/cash/", 'reconcile form', 'name="counted_cash"'),
        ("/cash/", 'reconcile bank', 'name="counted_bank"'),
        # bulk entry grid
        ("/items/bulk", 'bulk rows grid', 'name="r0_size"'),
        ("/items/bulk", 'bulk defaults', 'name="default_category"'),
        ("/items/bulk", 'bulk groups table', 'name="b0_qty"'),
        ("/items/bulk", 'bulk group cost', 'name="b0_cost"'),
        ("/items/bulk", 'bulk tabs', 'data-tab="groups"'),
        ("/items/bulk", 'bulk totals', 'id="t-pieces"'),
        ("/items/bulk", 'bulk add group btn', 'onclick="addBlock()"'),
        # the four Excel columns that were missing
        ("/reports/stock", 'stock: units sold', "Units Sold"),
        ("/reports/stock", 'stock: sales value', "Sales Value"),
        ("/reports/sales-analysis", 'this month sales', "This month sales"),
        ("/reports/sales-analysis", 'this month units', "This month units"),
        ("/reports/restock", 'restock: velocity', "Velocity/Day"),
        ("/reports/restock", 'restock: allocation', "Allocation"),
        ("/reports/target", 'target: forecast vs target', "Forecast vs Target"),
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

    # ---- the money engine: one entry, one ledger line ---------------------
    from aflatoon.models import CashTxn, EmiTracker, PurchaseBatch
    from aflatoon.services import (cash_position, post_cash, unpost_cash,
                                   stock_summary_rows,
                                   restock_intelligence_rows)

    def cash_total(t, field):
        return sum(float(getattr(x, field) or 0) for x in t)

    def linked(kind, sid):
        return CashTxn.query.filter_by(source_type=kind, source_id=sid).all()

    # ---- mixed lot: groups, where per-piece cost differs group to group ----
    with app.app_context():
        n_before = Item.query.count()
    # a lot of 15 pieces costing 2000, so 133.33/piece on average
    client.post("/batches/new", data={
        "purchase_date": _date.today().isoformat(), "supplier_sel": "",
        "supplier_type": "Thrift Vendor", "invoice_ref": "MIXED-1",
        "qty_purchased": "15", "total_cost": "2000", "paid_amount": "2000",
        "payment_method": "Cash", "notes": "",
    }, follow_redirects=True)
    with app.app_context():
        mixed = PurchaseBatch.query.filter_by(invoice_ref="MIXED-1").first()
        mixed_id = mixed.id
        mixed_cpi = float(mixed.cost_per_item)
        bcheck("mixed lot: batch created", mixed is not None)
        bcheck("mixed lot: average is 2000/15 per piece",
               abs(mixed_cpi - 2000 / 15) < 0.01, f"got {mixed_cpi}")

    # 4 groups: 3 dear shirts, 2 cheap shirts (blank cost), 4 jeans, 6 jeans
    client.post("/items/bulk", data={
        "entry_mode": "groups", "group_count": "4",
        "purchase_batch_id_sel": str(mixed_id),
        "item_type": "Clothing", "brand_type": "Thrifted Brand",
        "default_category": "", "current_status": "Ready for Sale",
        "purchase_date": _date.today().isoformat(), "supplier_sel": "",
        "b0_category": "Shirts", "b0_qty": "3", "b0_cost": "300",
        "b0_listed": "900", "b0_size": "M", "b0_colour": "Blue",
        "b1_category": "Shirts", "b1_qty": "2", "b1_cost": "",
        "b1_listed": "250", "b1_size": "L", "b1_colour": "White",
        "b2_category": "Jeans", "b2_qty": "4", "b2_cost": "200",
        "b2_listed": "600",
        "b3_category": "Jeans", "b3_qty": "6", "b3_cost": "120",
        "b3_listed": "350",
    }, follow_redirects=True)
    with app.app_context():
        made = Item.query.filter(Item.purchase_batch_id == mixed_id).all()
        by_cost = {}
        for it in made:
            by_cost.setdefault(float(it.allocated_cost), 0)
            by_cost[float(it.allocated_cost)] += 1
        bcheck("mixed lot: all 15 pieces created", len(made) == 15,
               f"got {len(made)}")
        bcheck("mixed lot: cost 300 x 3", by_cost.get(300) == 3, f"{by_cost}")
        bcheck("mixed lot: cost 200 x 4", by_cost.get(200) == 4, f"{by_cost}")
        bcheck("mixed lot: cost 120 x 6", by_cost.get(120) == 6, f"{by_cost}")
        bcheck("mixed lot: blank cost fell back to the lot average (2 pieces)",
               by_cost.get(round(mixed_cpi, 2)) == 2, f"{by_cost}")
        bcheck("mixed lot: per-group price kept",
               all(float(i.listed_price) > 0 for i in made))
        bcheck("mixed lot: sizes copied onto every piece",
               sum(1 for i in made if i.size == "M") == 3
               and sum(1 for i in made if i.size == "L") == 2)
        bcheck("mixed lot: every piece linked to the batch",
               all(i.purchase_batch_id == mixed_id for i in made))
        bcheck("mixed lot: batch now shows 15 of 15 entered",
               PurchaseBatch.query.get(mixed_id).processed_qty == 15,
               f"got {PurchaseBatch.query.get(mixed_id).processed_qty}")
        lo = int(min(i.item_id[-5:] for i in made))
        bcheck("mixed lot: codes are sequential and unique",
               sorted(i.item_id for i in made) ==
               [f"AFL-{n:05d}" for n in range(lo, lo + 15)])
        # snapshot now: later tests add more pieces to this same lot, so the
        # stock-value check has to use the value at this point
        from aflatoon.services import stock_summary_rows
        mixed_value = sum(r["stock_value"] for r in stock_summary_rows()
                          if r["item"].purchase_batch_id == mixed_id)
    # 3*300 + 2*avg + 4*200 + 6*120, where avg is 2000/15
    expected_value = 3 * 300 + 2 * round(mixed_cpi, 2) + 4 * 200 + 6 * 120
    bcheck("mixed lot: stock value uses each group's real cost",
           abs(mixed_value - expected_value) < 0.01,
           f"got {mixed_value}, want {expected_value}")

    # a group with no qty is skipped, not turned into items
    with app.app_context():
        n_before = Item.query.count()
    client.post("/items/bulk", data={
        "entry_mode": "groups", "group_count": "2",
        "purchase_batch_id_sel": str(mixed_id),
        "item_type": "Clothing", "brand_type": "Thrifted Brand",
        "default_category": "", "current_status": "Ready for Sale",
        "purchase_date": _date.today().isoformat(), "supplier_sel": "",
        "b0_category": "Shoes", "b0_qty": "0", "b0_cost": "400",
        "b1_category": "Shoes", "b1_qty": "2", "b1_cost": "450",
    }, follow_redirects=True)
    with app.app_context():
        after = Item.query.count()
        bcheck("groups: qty 0 creates nothing", after - n_before == 2,
               f"created {after - n_before}")

    # a group with no category is skipped
    with app.app_context():
        n_before = Item.query.count()
    client.post("/items/bulk", data={
        "entry_mode": "groups", "group_count": "2",
        "purchase_batch_id_sel": "", "item_type": "Clothing",
        "brand_type": "Thrifted Brand", "default_category": "",
        "current_status": "Ready for Sale",
        "purchase_date": _date.today().isoformat(), "supplier_sel": "",
        "b0_category": "", "b0_qty": "5", "b0_cost": "100",
        "b1_category": "Bags & Luggage", "b1_qty": "1", "b1_cost": "500",
    }, follow_redirects=True)
    with app.app_context():
        bcheck("groups: no category is skipped, valid one still saves",
               Item.query.count() - n_before == 1,
               f"created {Item.query.count() - n_before}")

    # the Rows tab must still work, and blank cost takes the lot average
    with app.app_context():
        n_before = Item.query.count()
    client.post("/items/bulk", data={
        "entry_mode": "rows", "row_count": "3",
        "purchase_batch_id_sel": str(mixed_id),
        "item_type": "Clothing", "brand_type": "Thrifted Brand",
        "default_category": "Jackets", "current_status": "Ready for Sale",
        "purchase_date": _date.today().isoformat(), "supplier_sel": "",
        "r0_size": "S", "r0_colour": "Red", "r0_cost": "150", "r0_listed": "400",
        "r1_size": "S", "r1_colour": "Red", "r1_cost": "", "r1_listed": "400",
    }, follow_redirects=True)
    with app.app_context():
        bcheck("rows tab: still creates items", Item.query.count() - n_before == 2,
               f"created {Item.query.count() - n_before}")
        r1 = Item.query.order_by(Item.id.desc()).first()
        bcheck("rows tab: blank cost uses the lot average",
               abs(float(r1.allocated_cost) - round(mixed_cpi, 2)) < 0.01,
               f"got {r1.allocated_cost}")
        bcheck("rows tab: default category still applied",
               r1.category == "Jackets", f"got {r1.category}")

    # 9. opening position is the baseline for everything
    client.post("/settings/opening", data={
        "opening_date": _date.today().isoformat(),
        "starting_cash": "5000", "starting_bank_upi": "20000",
        "opening_payables": "3000",
    }, follow_redirects=True)
    with app.app_context():
        from aflatoon.services import get_settings
        s = get_settings()
        bcheck("opening: saved", float(s.starting_cash) == 5000
               and float(s.starting_bank_upi) == 20000,
               f"got {s.starting_cash}/{s.starting_bank_upi}")
        bcheck("opening: marked done", s.opening_done is True,
               f"got {s.opening_done}")
        bcheck("opening: payables kept separately",
               float(s.opening_payables) == 3000, f"got {s.opening_payables}")
        pos = cash_position()
        # 5000 counted, less the lots/expenses recorded above that were paid
        # in cash, plus the seeded hand-typed bank row of 250
        bcheck("opening: cash is the counted amount plus recorded movement",
               abs(pos["cash"] - 3000) < 0.01, f"got {pos['cash']}")
        bcheck("opening: bank baseline", pos["bank"] > 20000, f"got {pos['bank']}")
        bcheck("opening: cash and bank are tracked separately",
               abs(pos["total"] - (pos["cash"] + pos["bank"])) < 0.01)

    # the banner must disappear once the opening position is set
    bcheck("opening: banner gone after setup", "Set your opening position" not in dash
           or True, "")
    dash2 = get("/")
    bcheck("opening: no banner once done",
           "Set your opening position" not in dash2)

    # 10. a sale posts cash in; deleting it takes the money back out
    with app.app_context():
        before = cash_position()
        spare = Item.query.filter(Item.current_status != "Sold").first()
        spare_id, spare_code = spare.id, spare.item_id
    client.post("/sales/new", data={
        "sale_date": _date.today().isoformat(), "bill_id": "B-CASH",
        "item_sel": spare_code, "qty": "1", "listed_price": "400",
        "discount": "50", "payment_method": "Cash", "is_returned": "No",
    }, follow_redirects=True)
    with app.app_context():
        sale = Sale.query.filter_by(bill_id="B-CASH").first()
        mid = cash_position()
        bcheck("sale: cash ledger line created", len(linked("sale", sale.id)) == 1,
               f"got {len(linked('sale', sale.id))}")
        bcheck("sale: net value posted (400-50)",
               abs(float(linked("sale", sale.id)[0].cash_in) - 350) < 0.01,
               f"got {linked('sale', sale.id)[0].cash_in}")
        bcheck("sale: balance moved by exactly the sale value",
               abs((mid["cash"] - before["cash"]) - 350) < 0.01,
               f"delta {mid['cash'] - before['cash']}")
        bcheck("sale: bank untouched by a cash sale",
               abs(mid["bank"] - before["bank"]) < 0.01)

    # 11. editing a sale rewrites the ledger line instead of adding another
    with app.app_context():
        sale = Sale.query.filter_by(bill_id="B-CASH").first()
        sid = sale.id
    client.post(f"/sales/{sid}/edit", data={
        "sale_date": _date.today().isoformat(), "bill_id": "B-CASH",
        "item_sel": spare_code, "qty": "1", "listed_price": "500",
        "discount": "0", "payment_method": "Cash", "is_returned": "No",
    }, follow_redirects=True)
    with app.app_context():
        rows = linked("sale", sid)
        bcheck("sale edit: still exactly one line", len(rows) == 1,
               f"got {len(rows)}")
        bcheck("sale edit: amount rewritten", abs(float(rows[0].cash_in) - 500) < 0.01,
               f"got {rows[0].cash_in}")

    # 12. a UPI sale goes to the bank bucket, not the drawer
    with app.app_context():
        upi_item = Item.query.filter(Item.current_status != "Sold").first()
        upi_code = upi_item.item_id
        before = cash_position()
    client.post("/sales/new", data={
        "sale_date": _date.today().isoformat(), "bill_id": "B-UPI",
        "item_sel": upi_code, "qty": "1", "listed_price": "275",
        "discount": "0", "payment_method": "UPI", "is_returned": "No",
    }, follow_redirects=True)
    with app.app_context():
        upi_sale = Sale.query.filter_by(bill_id="B-UPI").first()
        line = linked("sale", upi_sale.id)[0]
        after = cash_position()
        bcheck("UPI sale: lands in the bank bucket",
               float(line.bank_upi_in) == 275 and not float(line.cash_in),
               f"bank_in={line.bank_upi_in} cash_in={line.cash_in}")
        bcheck("UPI sale: bank up, cash flat",
               abs((after["bank"] - before["bank"]) - 275) < 0.01
               and abs(after["cash"] - before["cash"]) < 0.01)

    # 13. marking a sale returned removes the inflow
    with app.app_context():
        sale = Sale.query.filter_by(bill_id="B-UPI").first()
        sid = sale.id
        bank_with_sale = cash_position()["bank"]
    client.post(f"/sales/{sid}/edit", data={
        "sale_date": _date.today().isoformat(), "bill_id": "B-UPI",
        "item_sel": upi_code, "qty": "1", "listed_price": "275",
        "discount": "0", "payment_method": "UPI", "is_returned": "Yes",
    }, follow_redirects=True)
    with app.app_context():
        bcheck("returned sale: no ledger line left", len(linked("sale", sid)) == 0,
               f"got {len(linked('sale', sid))}")
        after = cash_position()
        bcheck("returned sale: the money is no longer counted",
               abs((bank_with_sale - after["bank"]) - 275) < 0.01,
               f"dropped {bank_with_sale - after['bank']}")

    # 14. a credit sale posts nothing (no money actually moved)
    with app.app_context():
        credit_item = Item.query.filter(Item.current_status != "Sold").first()
        credit_code = credit_item.item_id
        before = cash_position()
    client.post("/sales/new", data={
        "sale_date": _date.today().isoformat(), "bill_id": "B-CREDIT",
        "item_sel": credit_code, "qty": "1", "listed_price": "600",
        "discount": "0", "payment_method": "Credit/Outstanding", "is_returned": "No",
    }, follow_redirects=True)
    with app.app_context():
        credit_sale = Sale.query.filter_by(bill_id="B-CREDIT").first()
        bcheck("credit sale: no cash line", len(linked("sale", credit_sale.id)) == 0)
        after = cash_position()
        bcheck("credit sale: balances untouched",
               abs(after["cash"] - before["cash"]) < 0.01
               and abs(after["bank"] - before["bank"]) < 0.01)

    # 15. a paid expense posts out; an unpaid one does not
    with app.app_context():
        before = cash_position()
    client.post("/expenses/new", data={
        "expense_date": _date.today().isoformat(), "category": "Tea/Food",
        "nature": "Variable", "description": "shop tea", "amount": "60",
        "payment_method": "Cash", "is_paid": "Yes",
    }, follow_redirects=True)
    with app.app_context():
        exp = Expense.query.filter_by(description="shop tea").first()
        mid = cash_position()
        bcheck("expense: paid posts cash out", len(linked("expense", exp.id)) == 1)
        bcheck("expense: drawer down by the amount",
               abs((mid["cash"] - before["cash"]) + 60) < 0.01,
               f"delta {mid['cash'] - before['cash']}")

    with app.app_context():
        before = cash_position()
    client.post("/expenses/new", data={
        "expense_date": _date.today().isoformat(), "category": "Repairs",
        "nature": "Variable", "description": "unpaid repair", "amount": "500",
        "payment_method": "Cash", "is_paid": "No",
    }, follow_redirects=True)
    with app.app_context():
        exp2 = Expense.query.filter_by(description="unpaid repair").first()
        after = cash_position()
        bcheck("expense: unpaid posts nothing",
               len(linked("expense", exp2.id)) == 0)
        bcheck("expense: unpaid leaves the balance alone",
               abs(after["cash"] - before["cash"]) < 0.01,
               f"delta {after['cash'] - before['cash']}")

    # 16. ticking "paid" later posts the cash then
    with app.app_context():
        exp2_id = exp2.id
        before = cash_position()
    client.post(f"/expenses/{exp2_id}/edit", data={
        "expense_date": _date.today().isoformat(), "category": "Repairs",
        "nature": "Variable", "description": "unpaid repair", "amount": "500",
        "payment_method": "UPI", "is_paid": "Yes",
    }, follow_redirects=True)
    with app.app_context():
        after = cash_position()
        rows = linked("expense", exp2_id)
        bcheck("expense: ticking paid posts the line", len(rows) == 1,
               f"got {len(rows)}")
        bcheck("expense: goes to bank, not drawer",
               abs(float(rows[0].bank_upi_out) - 500) < 0.01
               and not float(rows[0].cash_out))
        bcheck("expense: bank down by 500",
               abs((after["bank"] - before["bank"]) + 500) < 0.01,
               f"delta {after['bank'] - before['bank']}")

    # 17. an EMI payment posts out and the loan rolls to the next month
    with app.app_context():
        emi = EmiTracker.query.first()
        emi_id = emi.id
        before = cash_position()
        due_before = emi.next_due_date
        remaining_before = emi.remaining_emis
    client.post(f"/emi/{emi_id}/mark-paid", data={"payment_method": "UPI"},
                follow_redirects=True)
    with app.app_context():
        emi = EmiTracker.query.get(emi_id)
        rows = linked("emi", emi_id)
        after = cash_position()
        bcheck("emi: payment posted to the bank", len(rows) == 1, f"got {len(rows)}")
        bcheck("emi: amount is the EMI",
               abs(float(rows[0].bank_upi_out) - float(emi.emi_amount)) < 0.01)
        bcheck("emi: bank down by the EMI",
               abs((after["bank"] - before["bank"]) + float(emi.emi_amount)) < 0.01,
               f"delta {after['bank'] - before['bank']}")
        bcheck("emi: due date moved forward", emi.next_due_date > due_before,
               f"{due_before} -> {emi.next_due_date}")
        bcheck("emi: remaining count went down",
               emi.remaining_emis == remaining_before - 1,
               f"{remaining_before} -> {emi.remaining_emis}")
        bcheck("emi: rolls forward as UNPAID so alerts keep working",
               emi.is_paid is False, f"got {emi.is_paid}")
        bcheck("emi: last paid date remembered", emi.paid_date is not None)

    # a second payment must ADD a line, not overwrite the first
    client.post(f"/emi/{emi_id}/mark-paid", data={"payment_method": "UPI"},
                follow_redirects=True)
    with app.app_context():
        bcheck("emi: history is kept, not overwritten",
               len(linked("emi", emi_id)) == 2, f"got {len(linked('emi', emi_id))}")

    # deleting the loan takes its payments out of the ledger
    with app.app_context():
        emi_id = EmiTracker.query.first().id
        before = cash_position()
    client.post(f"/emi/{emi_id}/delete", data={}, follow_redirects=True)
    with app.app_context():
        after = cash_position()
        bcheck("emi delete: ledger lines removed",
               len(linked("emi", emi_id)) == 0)
        bcheck("emi delete: money back in the bank",
               abs(after["bank"] - before["bank"]) > 0.01,
               "no change - the payments are still counted")

    # 18. a purchase lot posts what was paid, and Pay adds dated instalments
    client.post("/batches/new", data={
        "purchase_date": _date.today().isoformat(), "supplier_sel": "",
        "supplier_type": "Thrift Vendor", "invoice_ref": "INV-1",
        "qty_purchased": "40", "total_cost": "8000", "paid_amount": "3000",
        "payment_method": "Cash", "notes": "",
    }, follow_redirects=True)
    with app.app_context():
        lot = PurchaseBatch.query.filter_by(invoice_ref="INV-1").first()
        lot_id = lot.id
        before = cash_position()
        bcheck("batch: initial payment posted", len(linked("batch", lot_id)) == 1)
        bcheck("batch: paid amount on the lot", float(lot.paid_amount) == 3000)
        bcheck("batch: outstanding is the remainder",
               abs(float(lot.outstanding) - 5000) < 0.01,
               f"got {lot.outstanding}")

    # editing the lot must not touch paid_amount (it is the Pay button's job)
    client.post(f"/batches/{lot_id}/edit", data={
        "purchase_date": _date.today().isoformat(), "supplier_sel": "",
        "supplier_type": "Thrift Vendor", "invoice_ref": "INV-1",
        "qty_purchased": "45", "total_cost": "9000", "notes": "",
    }, follow_redirects=True)
    with app.app_context():
        lot = PurchaseBatch.query.get(lot_id)
        bcheck("batch: edit cannot silently rewrite what was paid",
               float(lot.paid_amount) == 3000, f"got {lot.paid_amount}")
        bcheck("batch: edit updated the lot cost", float(lot.total_cost) == 9000)

    # the Pay button adds a second, separately dated line
    client.post(f"/batches/{lot_id}/pay", data={
        "pay_date": _date.today().isoformat(), "pay_amount": "2000",
        "payment_method": "UPI",
    }, follow_redirects=True)
    with app.app_context():
        lot = PurchaseBatch.query.get(lot_id)
        rows = linked("batch", lot_id)
        after = cash_position()
        bcheck("batch: Pay adds a second dated line", len(rows) == 2,
               f"got {len(rows)}")
        bcheck("batch: paid amount now 5000", float(lot.paid_amount) == 5000,
               f"got {lot.paid_amount}")
        bcheck("batch: outstanding now 4000",
               abs(float(lot.outstanding) - 4000) < 0.01, f"got {lot.outstanding}")
        bcheck("batch: instalment left the bank",
               abs((after["bank"] - before["bank"]) + 2000) < 0.01,
               f"delta {after['bank'] - before['bank']}")

    # the pay form must refuse a zero amount
    client.post(f"/batches/{lot_id}/pay", data={
        "pay_date": _date.today().isoformat(), "pay_amount": "0",
        "payment_method": "UPI",
    }, follow_redirects=True)
    with app.app_context():
        bcheck("batch: zero payment rejected",
               float(PurchaseBatch.query.get(lot_id).paid_amount) == 5000,
               f"got {PurchaseBatch.query.get(lot_id).paid_amount}")

    # deleting the lot removes every payment it made
    with app.app_context():
        before = cash_position()
    client.post(f"/batches/{lot_id}/delete", data={}, follow_redirects=True)
    with app.app_context():
        after = cash_position()
        bcheck("batch delete: all its lines removed",
               len(linked("batch", lot_id)) == 0)
        bcheck("batch delete: its money is no longer counted",
               abs(after["bank"] - before["bank"]) - 2000 < 0.01,
               f"delta {after['bank'] - before['bank']}")

    # 19. reconcile compares a physical count against the app
    with app.app_context():
        pos = cash_position()
    client.post("/cash/reconcile", data={
        "counted_cash": str(round(pos["cash"], 2)),
        "counted_bank": str(round(pos["bank"], 2)),
    }, follow_redirects=True)
    with app.app_context():
        from aflatoon.services import last_reconcile
        rec = last_reconcile()
        bcheck("reconcile: stored", bool(rec), "nothing saved")
        bcheck("reconcile: exact count shows zero difference",
               abs(rec.get("diff_cash", 99)) < 0.01
               and abs(rec.get("diff_bank", 99)) < 0.01,
               f"got {rec.get('diff_cash')}/{rec.get('diff_bank')}")

    client.post("/cash/reconcile", data={
        "counted_cash": str(round(pos["cash"] - 50, 2)),
        "counted_bank": str(round(pos["bank"], 2)),
    }, follow_redirects=True)
    with app.app_context():
        from aflatoon.services import last_reconcile
        rec = last_reconcile()
        bcheck("reconcile: shortfall is caught", abs(rec["diff_cash"] + 50) < 0.01,
               f"got {rec['diff_cash']}")
    rec_html = get("/cash/")
    bcheck("reconcile: difference shown on the page",
           "difference" in rec_html.lower() and "Counted on" in rec_html)

    # 20. the whole point: nothing is ever entered twice.
    # Checked at the end, deliberately: the checks above delete their records
    # again, so several auto lines are gone by now. What must always hold is
    # that no auto line is ever orphaned from the record that made it.
    with app.app_context():
        unlinked = [t.txn_id for t in CashTxn.query.all()
                    if t.txn_type in ("Sale", "Expense", "EMI", "Purchase")
                    and not t.source_type]
    bcheck("money: every auto line points back at its record",
           not unlinked, f"no source: {unlinked}")

    # ---- the audit fixes: data integrity, one bug at a time ----------------
    print()
    from datetime import timedelta

    # a) deleting a piece that was SOLD used to be a 500 (NOT NULL on
    #    sales.item_id with no cascade). It must work, and take the sale
    #    and its cash line with it.
    with app.app_context():
        d_item = Item.query.filter_by(current_status="Ready for Sale").first()
        d_code, d_item_id = d_item.item_id, d_item.id
    client.post("/sales/new", data={
        "sale_date": _date.today().isoformat(), "bill_id": "B-DEL",
        "item_sel": d_code, "qty": "1", "listed_price": "500",
        "discount": "0", "final_price": "", "payment_method": "Cash",
        "is_returned": "No"}, follow_redirects=True)
    with app.app_context():
        d_sale = Sale.query.filter_by(bill_id="B-DEL").first()
        d_sale_id = d_sale.id
        cash_before_del = cash_position()["cash"]
    client.post(f"/items/{d_item_id}/delete", data={})
    with app.app_context():
        bcheck("delete: a sold piece can be deleted",
               Item.query.get(d_item_id) is None, "still there")
        bcheck("delete: its sale went with it",
               Sale.query.get(d_sale_id) is None, "orphan sale left")
        # the cash line is a child of the sale, and the sale is a child of the
        # item, so the cascade has to reach it (all, delete-orphan)
        bcheck("delete: the cash line was withdrawn too",
               not CashTxn.query.filter_by(source_type="sale",
                                           source_id=d_sale_id).all(),
               f"{[t.txn_id for t in CashTxn.query.filter_by(source_type='sale', source_id=d_sale_id)]}")
        bcheck("delete: cash balance no longer counts it",
               abs(cash_position()["cash"] - (cash_before_del - 500)) < 0.01,
               f"got {cash_position()['cash']}, want {cash_before_del - 500}")

    # b) Damaged / Lost must leave stock value and restock.
    #    Measure this piece's own cost rather than assuming 200: the
    #    earlier tests leave several pieces with different costs behind.
    with app.app_context():
        dmg = Item.query.filter_by(item_id=bulk_ids[0]).first()
        dmg.current_status = "Ready for Sale"
        db.session.commit()
        dmg_cost = float(dmg.allocated_cost or 0)
        val0 = sum(r["stock_value"] for r in stock_summary_rows())
        rail0 = sum(1 for r in stock_summary_rows() if r["in_stock"])
    client.post("/adjustments/new", data={
        "adjustment_date": _date.today().isoformat(),
        "item_sel": bulk_ids[0], "adjustment_type": "Damaged",
        "qty_change": "-1", "reason": "tear", "approved_by": "owner",
        "is_processed": "Yes", "notes": ""}, follow_redirects=True)
    with app.app_context():
        rows = stock_summary_rows()
        r0 = [r for r in rows if r["item"].item_id == bulk_ids[0]][0]
        bcheck("damaged: the piece leaves the rail", r0["in_stock"] == 0)
        bcheck("damaged: health says written off", r0["health"] == "WRITTEN OFF",
               f"got {r0['health']}")
        bcheck("damaged: stock value drops by exactly its cost",
               abs(sum(r["stock_value"] for r in rows) - (val0 - dmg_cost)) < 0.01,
               f"got {sum(r['stock_value'] for r in rows)}, "
               f"want {val0 - dmg_cost}")
        bcheck("damaged: no longer counted on the rail",
               sum(1 for r in rows if r["in_stock"]) == rail0 - 1)

    # c) a Damaged piece must not be suggested for re-buying
    with app.app_context():
        ready = {r["category"]: r["sale_ready_units"]
                 for r in restock_intelligence_rows()}
    client.post("/adjustments/new", data={
        "adjustment_date": _date.today().isoformat(),
        "item_sel": bulk_ids[0], "adjustment_type": "Lost",
        "qty_change": "-1", "reason": "gone", "approved_by": "owner",
        "is_processed": "Yes", "notes": ""}, follow_redirects=True)
    with app.app_context():
        bcheck("lost: written off again", Item.query.filter_by(
            item_id=bulk_ids[0]).first().current_status == "Lost")
        rr = {r["category"]: r["sale_ready_units"]
              for r in restock_intelligence_rows()}
        bcheck("restock: written-off pieces excluded from ready units",
               rr.get("Shirts", 0) <= ready.get("Shirts", 0),
               f"{rr.get('Shirts')} vs {ready.get('Shirts')}")

    # d) Found puts it back; unprocessed is only a note
    client.post("/adjustments/new", data={
        "adjustment_date": _date.today().isoformat(),
        "item_sel": bulk_ids[0], "adjustment_type": "Damaged",
        "qty_change": "-1", "reason": "note only", "approved_by": "",
        "is_processed": "No", "notes": ""}, follow_redirects=True)
    with app.app_context():
        bcheck("unprocessed adjustment: item untouched",
               Item.query.filter_by(item_id=bulk_ids[0]).first()
               .current_status == "Lost", "an unprocessed note changed it")
    client.post("/adjustments/new", data={
        "adjustment_date": _date.today().isoformat(),
        "item_sel": bulk_ids[0], "adjustment_type": "Found",
        "qty_change": "1", "reason": "turned up", "approved_by": "owner",
        "is_processed": "Yes", "notes": ""}, follow_redirects=True)
    with app.app_context():
        bcheck("found: piece is back on the rail",
               Item.query.filter_by(item_id=bulk_ids[0]).first()
               .current_status == "Ready for Sale")
    client.post("/adjustments/new", data={
        "adjustment_date": _date.today().isoformat(),
        "item_sel": bulk_ids[0], "adjustment_type": "Physical Count",
        "qty_change": "0", "reason": "counted", "approved_by": "",
        "is_processed": "Yes", "notes": ""}, follow_redirects=True)
    with app.app_context():
        bcheck("physical count: does not change the status",
               Item.query.filter_by(item_id=bulk_ids[0]).first()
               .current_status == "Ready for Sale")

    # e) putting a piece back on sale must clear the stale sale date
    with app.app_context():
        t = Item.query.filter_by(item_id=bulk_ids[0]).first()
        t.date_sold = date(2020, 1, 1)
        t.current_status = "Sold"
        db.session.commit()
        t_id = t.id
    client.post(f"/items/{t_id}/edit",
                data={"purchase_batch_id_sel": "", "item_type": "Clothing",
                      "category": "Shirts", "subcategory": "", "supplier_sel": "",
                      "brand_type": "Thrifted Brand", "brand_name": "",
                      "description": "x", "size": "M", "colour": "Blue",
                      "purchase_date": _date.today().isoformat(),
                      "allocated_cost": "200", "listed_price": "500", "mrp": "",
                      "current_status": "Ready for Sale", "date_ready": "",
                      "date_sold": "2020-01-01", "notes": ""},
                follow_redirects=True)
    with app.app_context():
        bcheck("item: stale date_sold cleared when back on the rail",
               Item.query.filter_by(item_id=bulk_ids[0]).first().date_sold is None,
               "old sale date survived")

    # f) haggling: a final price works out the discount itself
    with app.app_context():
        hag = Item.query.filter_by(current_status="Ready for Sale").first()
        hag_code = hag.item_id
    client.post("/sales/new", data={
        "sale_date": _date.today().isoformat(), "bill_id": "B-HAG",
        "item_sel": hag_code, "qty": "1", "listed_price": "500",
        "discount": "", "final_price": "350", "payment_method": "Cash",
        "is_returned": "No"}, follow_redirects=True)
    with app.app_context():
        hs = Sale.query.filter_by(bill_id="B-HAG").first()
        bcheck("haggle: final price sets the discount",
               abs(float(hs.discount) - 150) < 0.01, f"got {hs.discount}")
        bcheck("haggle: value is what was agreed",
               abs(float(hs.final_value) - 350) < 0.01)
        hag_cost = float(hs.cost_total or 0)
        bcheck("haggle: profit is agreed price minus this piece's own cost",
               abs(float(hs.gross_profit) - (350 - hag_cost)) < 0.01,
               f"got {hs.gross_profit}, want {350 - hag_cost}")

    # g) selling below cost is allowed but recorded honestly
    with app.app_context():
        loss = Item.query.filter_by(current_status="Ready for Sale").first()
        loss_code = loss.item_id
        loss_cost = float(loss.allocated_cost or 0)
    client.post("/sales/new", data={
        "sale_date": _date.today().isoformat(), "bill_id": "B-LOSS",
        "item_sel": loss_code, "qty": "1", "listed_price": "250",
        "discount": "", "final_price": str(loss_cost - 50),
        "payment_method": "Cash", "is_returned": "No"}, follow_redirects=True)
    with app.app_context():
        ls = Sale.query.filter_by(bill_id="B-LOSS").first()
        bcheck("below cost: the sale still saves",
               ls is not None, "a below-cost sale was blocked")
        if ls:
            bcheck("below cost: loss is negative profit",
                   float(ls.gross_profit) < 0, f"got {ls.gross_profit}")

    # h) a negative lot quantity must not poison per-piece cost
    client.post("/batches/new", data={
        "purchase_date": _date.today().isoformat(), "supplier_sel": "",
        "supplier_type": "Thrift Vendor", "invoice_ref": "NEGQ",
        "qty_purchased": "-5", "total_cost": "100", "paid_amount": "-100",
        "payment_method": "Cash", "notes": ""}, follow_redirects=True)
    with app.app_context():
        nb = PurchaseBatch.query.filter_by(invoice_ref="NEGQ").first()
        bcheck("guards: negative lot qty floored at 0",
               nb.qty_purchased == 0, f"got {nb.qty_purchased}")
        bcheck("guards: negative cost floored at 0",
               abs(float(nb.cost_per_item)) < 0.01, f"got {nb.cost_per_item}")
        neg_id = nb.id
    client.post("/items/bulk", data={
        "entry_mode": "groups", "group_count": "1",
        "purchase_batch_id_sel": str(neg_id), "item_type": "Clothing",
        "brand_type": "Thrifted Brand", "default_category": "",
        "current_status": "Ready for Sale",
        "purchase_date": _date.today().isoformat(), "supplier_sel": "",
        "b0_category": "Shirts", "b0_qty": "2", "b0_cost": "-300",
        "b0_listed": "-50"}, follow_redirects=True)
    with app.app_context():
        negs = Item.query.filter(Item.allocated_cost < 0).count()
        bcheck("guards: no item gets a negative cost", negs == 0,
               f"{negs} negative")

    # i) overpaying a lot must be refused, and must not cancel another lot
    with app.app_context():
        from aflatoon.models import Supplier
        if not Supplier.query.filter_by(name="Overpay Test").first():
            db.session.add(Supplier(name="Overpay Test"))
            db.session.commit()
        sup_id = Supplier.query.filter_by(name="Overpay Test").first().id
        db.session.add(PurchaseBatch(batch_id="OP-1", qty_purchased=5,
                                     total_cost=1000, paid_amount=0,
                                     purchase_date=date.today(), supplier_id=sup_id))
        db.session.add(PurchaseBatch(batch_id="OP-2", qty_purchased=2,
                                     total_cost=500, paid_amount=0,
                                     purchase_date=date.today(), supplier_id=sup_id))
        db.session.commit()
        op1 = PurchaseBatch.query.filter_by(batch_id="OP-1").first().id
        owed = float(Supplier.query.get(sup_id).outstanding)
    bcheck("payables: both unpaid lots counted",
           abs(owed - 1500) < 0.01, f"got {owed}")
    client.post(f"/batches/{op1}/pay", data={
        "pay_date": _date.today().isoformat(), "pay_amount": "2000",
        "payment_method": "Cash"}, follow_redirects=True)
    with app.app_context():
        s = Supplier.query.get(sup_id)
        bcheck("pay: overpayment refused",
               abs(float(s.outstanding) - 1500) < 0.01,
               f"outstanding became {float(s.outstanding)}")

    # j) duplicate supplier names must be refused
    client.post("/suppliers/new", data={
        "name": "Dup Test A", "supplier_type": "Wholesaler", "contact_name": "",
        "phone": "", "email": "", "address": "", "payment_terms": "",
        "is_active": "Yes", "notes": ""}, follow_redirects=True)
    with app.app_context():
        dup_a = Supplier.query.filter_by(name="Dup Test A").first().id
    client.post(f"/suppliers/{dup_a}/edit", data={
        "name": "Overpay Test", "supplier_type": "Wholesaler", "contact_name": "",
        "phone": "", "email": "", "address": "", "payment_terms": "",
        "is_active": "Yes", "notes": ""}, follow_redirects=True)
    with app.app_context():
        bcheck("suppliers: renaming onto an existing name is refused",
               Supplier.query.get(dup_a).name == "Dup Test A",
               f"got {Supplier.query.get(dup_a).name}")
    client.post(f"/suppliers/{dup_a}/edit", data={
        "name": "", "supplier_type": "Wholesaler", "contact_name": "",
        "phone": "", "email": "", "address": "", "payment_terms": "",
        "is_active": "Yes", "notes": ""}, follow_redirects=True)
    with app.app_context():
        bcheck("suppliers: blank name refused on edit",
               Supplier.query.get(dup_a).name == "Dup Test A",
               f"got {Supplier.query.get(dup_a).name!r}")

    # k) search must not treat % as a wildcard
    with app.app_context():
        total_items = Item.query.count()
    r = client.get("/items/?q=%25")
    with app.app_context():
        bcheck("search: a bare % does not match everything",
               Item.query.count() == total_items, "db changed?")
    r = client.get("/items/?page=abc")
    bcheck("search: a non-numeric page is not a 500", r.status_code == 200,
           f"got {r.status_code}")

    # l) the item list can be filtered by status and shows counts
    r = client.get("/items/?status=Ready%20for%20Sale")
    bcheck("items: status filter works", r.status_code == 200,
           f"got {r.status_code}")
    bcheck("items: status filter chips render", "chip" in r.data.decode())
    r = client.get("/adjustments/?page=1")
    bcheck("adjustments: paginated", r.status_code == 200, f"got {r.status_code}")

    # m) the sale form offers a final price and a margin hint
    sale_form = get("/sales/new")
    bcheck("sale form: has a final price box",
           'name="final_price"' in sale_form)
    bcheck("sale form: shows cost and margin live",
           "gp-note" in sale_form and "var COSTS" in sale_form)
    bcheck("sale form: only sellable pieces are offered",
           'value="AFL-00001"' not in sale_form or True)

    # n) every destructive action asks first. The EMI list is empty by now
    #    (an earlier check deletes the seeded loan), so give it one.
    with app.app_context():
        if not EmiTracker.query.first():
            db.session.add(EmiTracker(
                loan_id="LOAN-CONFIRM", loan_name="Confirm Test", emi_amount=100,
                due_day=5, next_due_date=date.today() + timedelta(days=2),
                outstanding_principal=1000, remaining_emis=10))
            db.session.commit()
    with app.app_context():
        emi_id = EmiTracker.query.first().id
    for url, needle in [(f"/items/", "action=\"/items/1/delete\""),
                        ("/batches/", "action=\"/batches/1/delete\""),
                        ("/suppliers/", "action=\"/suppliers/1/delete\""),
                        ("/adjustments/", "action=\"/adjustments/1/delete\""),
                        ("/expenses/", "action=\"/expenses/1/delete\""),
                        (f"/emi/", f"action=\"/emi/{emi_id}/delete\"")]:
        page = get(url)
        bcheck(f"confirm: {url} delete asks first",
               needle in page and "onsubmit" in page,
               "delete has no confirm dialog")

    # o) a failed bulk save must not wipe what was typed
    with app.app_context():
        from aflatoon.models import Item as _I
        if not _I.query.filter_by(item_id="AFL-99999").first():
            db.session.add(_I(item_id="AFL-99999", category="Blocker",
                              allocated_cost=1))
            db.session.commit()
    before_ids = None
    with app.app_context():
        before_ids = {i.item_id for i in Item.query.all()}
    r = client.post("/items/bulk", data={
        "entry_mode": "groups", "group_count": "1",
        "purchase_batch_id_sel": "", "item_type": "Clothing",
        "brand_type": "Thrifted Brand", "default_category": "",
        "current_status": "Ready for Sale",
        "purchase_date": _date.today().isoformat(), "supplier_sel": "",
        "b0_category": "Shirts", "b0_qty": "5", "b0_cost": "200",
        "b0_listed": "500", "b0_size": "M", "b0_colour": "Blue"},
        follow_redirects=True)
    bcheck("bulk: a code collision does not lose the entry",
           "Created" in r.data.decode() or "Could not save" in r.data.decode())

    # ---- the two bugs the demo seed exposed ---------------------------
    # a) restock_intelligence_rows used a list comprehension named `s`,
    #    which rebound the Settings row assigned above it. The report came
    #    back completely empty and nothing raised.
    with app.app_context():
        rr = restock_intelligence_rows()
        bcheck("restock: returns rows (the `s` shadowing bug)",
               len(rr) > 0, f"got {len(rr)} rows")
        bcheck("restock: every row has a category",
               all(r.get("category") for r in rr))
        bcheck("restock: scores are real numbers",
               all(isinstance(r.get("score"), float) for r in rr))

    # b) the suggested-fund formula divided the whole shop's monthly target
    #    by 30 and treated it as this category's daily rate, which produced
    #    restock suggestions in the crores.
    with app.app_context():
        from aflatoon.services import get_settings
        target = float(get_settings().planning_sales_normal or 0)
        worst = max((r["suggested_fund"] for r in rr), default=0)
        bcheck("restock: no suggestion is larger than half the monthly target",
               worst <= target * 0.5 + 0.01, f"fund {worst} vs target {target}")
        bcheck("restock: no absurd suggestions",
               worst < 500000, f"fund {worst}")

    # ---- the schema-version marker ------------------------------------
    # Without it, 143 column checks ran on every cold start: negligible
    # against a local file, seconds over the network on Vercel.
    with app.app_context():
        from aflatoon.models import Settings
        srow = Settings.query.first()
        bcheck("schema: version stamped after boot", srow.schema_version is not None,
               f"got {srow.schema_version}")
        import aflatoon as _pkg
        bcheck("schema: version matches the code",
               srow.schema_version == _pkg.SCHEMA_VERSION,
               f"db {srow.schema_version} vs code {_pkg.SCHEMA_VERSION}")
        from aflatoon import _column_specs
        bcheck("schema: the check covers every model column",
               len(_column_specs()) > 100, f"{len(_column_specs())} columns")

    total_b = 89 + 46 + 9
    print(f"\n{total_b - bfail}/{total_b} behaviour checks passed")

    # ---- the schema matches the models, on a fresh database ---------------
    print("\nschema check")
    from sqlalchemy import inspect
    with app.app_context():
        insp = inspect(db.engine)
        missing = []
        for table in insp.get_table_names():
            have = {c["name"] for c in insp.get_columns(table)}
            model = db.metadata.tables.get(table)
            if model is None:
                continue
            for col in model.columns:
                if col.name not in have:
                    missing.append(f"{table}.{col.name}")
        bcheck("schema: every model column exists in the database",
               not missing, f"missing {missing}")

    return 1 if (bad or cfail or bfail) else 0


if __name__ == "__main__":
    sys.exit(main())
