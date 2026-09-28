"""Sample trading data, so the app can be shown with something on screen.

This is DEMO data for a friend to click through, not anyone's real
accounts. It is only ever written to an empty database, and only when
SEED_DEMO_DATA=1 is set - never on the owner's real records.

The data is shaped to exercise the parts of the app that are hard to judge
from an empty screen: stock that has been sitting for different lengths of
time (so the aging columns differ), a lost piece and a damaged one (so the
write-off path is visible), a part-paid purchase lot (so Outstanding shows
real money), loans with a payment history, and sales spread over three
months (so the trend, target and restock reports have a shape).
"""

from datetime import date, timedelta
from decimal import Decimal

from aflatoon.extensions import db
from aflatoon.models import (Supplier, PurchaseBatch, Item, Sale, Expense,
                             EmiTracker, CashTxn, StockAdjustment, Settings)

# months back from today, used so the reports always have a recent history
TODAY = date.today()


def _d(days_ago: int) -> date:
    return TODAY - timedelta(days=days_ago)


def _first_of_month(d: date) -> date:
    return d.replace(day=1)


def seed_if_empty() -> bool:
    """Write the demo data if the database has no items yet.

    Returns True if it seeded. Safe to call on every boot: it checks first,
    so a warm function instance or a second cold start will not duplicate
    anything or overwrite what the owner has since entered.
    """
    from aflatoon.models import Item as _Item
    if _Item.query.count() > 0:
        return False
    seed_demo()
    return True


def seed_demo() -> None:
    from aflatoon.services import get_settings

    s = get_settings()
    s.opening_date = _first_of_month(_d(75))
    s.opening_done = True
    # a float big enough that the purchases and expenses below can be paid
    # for out of it, so the cash balance never goes negative
    s.starting_cash = Decimal("12000")
    s.starting_bank_upi = Decimal("185000")
    s.opening_payables = Decimal("0")
    s.planning_sales_normal = Decimal("60000")
    s.planning_sales_bad = Decimal("40000")
    s.planning_sales_good = Decimal("85000")
    s.est_new_items_per_month = 130

    # ---- suppliers ----------------------------------------------------
    rehman = Supplier(name="Rehman Stores", supplier_type="Thrift Vendor",
                      contact_name="Imran", phone="98XXXXXX01",
                      payment_terms="Cash on delivery", is_active=True)
    khan = Supplier(name="Khan Textiles", supplier_type="Wholesaler",
                    contact_name="Salma", phone="98XXXXXX02",
                    payment_terms="Net 15", is_active=True)
    old = Supplier(name="Saba Collection", supplier_type="Thrift Vendor",
                   contact_name="Nadia", phone="98XXXXXX03",
                   payment_terms="Cash", is_active=True)
    db.session.add_all([rehman, khan, old])
    db.session.commit()

    # ---- purchase lots -------------------------------------------------
    # Sized like the real thing: he buys 100-200 pieces in a lot, so three
    # months of stock is about 400 pieces. Costs are set at roughly a third
    # of the selling price, which is normal thrift margin, and the three
    # lots are one fully paid, one part paid (money genuinely owed, so
    # Outstanding shows real cash) and one bought entirely on credit.
    lots = [
        PurchaseBatch(batch_id="PUR-00001", purchase_date=_d(70),
                      supplier_id=rehman.id, supplier_type="Thrift Vendor",
                      invoice_ref="RS-441", qty_purchased=150,
                      total_cost=Decimal("49500"),
                      paid_amount=Decimal("49500")),
        PurchaseBatch(batch_id="PUR-00002", purchase_date=_d(38),
                      supplier_id=khan.id, supplier_type="Wholesaler",
                      invoice_ref="KT-88", qty_purchased=130,
                      total_cost=Decimal("52000"),
                      paid_amount=Decimal("34000")),
        PurchaseBatch(batch_id="PUR-00003", purchase_date=_d(12),
                      supplier_id=old.id, supplier_type="Thrift Vendor",
                      invoice_ref="", qty_purchased=120,
                      total_cost=Decimal("38400"), paid_amount=Decimal("0")),
    ]
    db.session.add_all(lots)
    db.session.commit()

    # ---- the pieces ---------------------------------------------------
    # (batch, category, subcategory, size, colour, cost, listed, days_held)
    piece_defs = [
        (lots[0], "Dresses", "Maxi", "M", "Maroon", 400, 1200, 68),
        (lots[0], "Dresses", "Maxi", "L", "Green", 400, 1200, 68),
        (lots[0], "Dresses", "Kurta", "XL", "Cream", 380, 1100, 66),
        (lots[0], "Shirts", "Casual", "M", "Blue", 350, 900, 64),
        (lots[0], "Shirts", "Casual", "L", "White", 350, 900, 64),
        (lots[0], "Jeans", "Straight", "32", "Indigo", 420, 1300, 61),
        (lots[0], "Jeans", "Straight", "34", "Black", 420, 1300, 60),
        (lots[0], "Jackets", "Denim", "L", "Blue", 700, 2200, 58),
        (lots[1], "T-Shirts", "Graphic", "M", "Black", 200, 600, 35),
        (lots[1], "T-Shirts", "Graphic", "M", "White", 200, 600, 35),
        (lots[1], "T-Shirts", "Plain", "S", "Grey", 180, 500, 34),
        (lots[1], "Trousers", "Formal", "32", "Charcoal", 480, 1500, 32),
        (lots[1], "Trousers", "Formal", "34", "Black", 480, 1500, 30),
        (lots[1], "Shoes", "Heels", "5", "Tan", 700, 2000, 28),
        (lots[1], "Shoes", "Flats", "7", "Black", 650, 1800, 27),
        (lots[2], "Kurtas", "Cotton", "L", "Mustard", 320, 1000, 11),
        (lots[2], "Kurtas", "Cotton", "XL", "Pink", 320, 1000, 10),
        (lots[2], "Accessories", "Belt", "32", "Brown", 150, 500, 9),
        # a healthy amount still on the rail, at ages that make the
        # slow-moving and dead-stock columns actually differ
        (lots[2], "Shirts", "Linen", "M", "Sky", 340, 950, 8),
        (lots[2], "Shirts", "Linen", "L", "Olive", 340, 950, 8),
        (lots[2], "Dresses", "Wrap", "M", "Rust", 380, 1150, 7),
        (lots[2], "Jeans", "Skinny", "28", "Blue", 400, 1250, 7),
        (lots[2], "Jeans", "Skinny", "30", "Blue", 400, 1250, 6),
        (lots[1], "Shoes", "Sandals", "6", "Brown", 550, 1500, 26),
        (lots[1], "Bags & Luggage", "Handbag", "One Size", "Tan", 500, 1400, 25),
        (lots[1], "Soft Toys", "Teddy", "One Size", "Beige", 200, 600, 24),
        (lots[0], "Jackets", "Bomber", "M", "Olive", 650, 2000, 57),
        (lots[0], "T-Shirts", "Plain", "L", "Navy", 190, 550, 55),
        (lots[1], "Trousers", "Cargo", "34", "Khaki", 520, 1600, 31),
    ]
    # Expand the definitions into the number of pieces each lot actually
    # says were bought, so the batch progress ("24 entered of 24") adds up.
    # A thrift lot really is 100-200 near-identical pieces, and a demo with
    # six dresses in a 24-piece lot looks fake on the stock report.
    items = []
    n = 0
    for lot_index, (lot, age) in enumerate([(lots[0], 68), (lots[1], 35),
                                            (lots[2], 10)]):
        same_lot = [p for p in piece_defs if p[0] is lot]
        for k in range(lot.qty_purchased):
            _lot, cat, sub, size, colour, cost, listed, _age = \
                same_lot[k % len(same_lot)]
            # a small natural spread in cost, the way a real lot has
            bumped = cost + (k % 5) * 10
            n += 1
            items.append(Item(
                item_id=f"AFL-{n:05d}", purchase_batch_id=lot.id,
                supplier_id=lot.supplier_id, item_type="Clothing",
                category=cat, subcategory=sub, brand_type="Thrifted Brand",
                brand_name="", description=f"{sub} {colour.lower()}",
                size=size, colour=colour, purchase_date=_d(age),
                allocated_cost=Decimal(bumped), listed_price=Decimal(listed),
                mrp=Decimal(int(listed * 1.8)),
                current_status="Ready for Sale", date_ready=_d(age)))
    db.session.add_all(items)
    db.session.commit()

    # ---- a lost piece and a damaged one --------------------------------
    # two pieces from the newest lot, so the write-off reports have rows
    items[-6].current_status = "Lost"
    items[-5].current_status = "Damaged"

    # ---- sales, spread across three months -----------------------------
    # The volume is what makes the profit report believable: around 55
    # sales a month at a ~Rs 1100 average is roughly Rs 60k of turnover a
    # month, which comfortably covers rent and the small running costs and
    # is the kind of figure this shop would actually be doing. Newest stock
    # sells first, so the aging columns differ across the report.
    total = len(items)
    sold_count = int(total * 0.44)
    sales_defs = []
    next_bill = 1000
    for i in range(sold_count):
        it = items[i]
        # spread sales evenly across the last 80 days
        age = 3 + (i * 80) // max(sold_count - 1, 1)
        # haggling happens: roughly a third of sales carry a discount
        disc = int(float(it.listed_price) * (0.1 if i % 3 == 0 else 0.0))
        method = ["Cash", "UPI", "Cash", "Card"][i % 4]
        next_bill += 1
        sales_defs.append((i, age, int(it.listed_price), disc, method,
                           f"B-{next_bill}"))
    sales = []
    for idx, ago, listed, disc, method, bill in sales_defs:
        it = items[idx]
        it.current_status = "Sold"
        it.date_sold = _d(ago)
        sales.append(Sale(sale_date=_d(ago), bill_id=bill, item_id=it.id,
                          qty=1, listed_price=Decimal(listed),
                          discount=Decimal(disc), payment_method=method,
                          is_returned=False))
    db.session.add_all(sales)
    db.session.commit()

    # one returned sale, so that screen is not empty either
    ret = items[sold_count + 2]
    db.session.add(Sale(sale_date=_d(6), bill_id=f"B-{next_bill + 1}",
                        item_id=ret.id, qty=1,
                        listed_price=Decimal(ret.listed_price or 0),
                        discount=Decimal("0"), payment_method="Cash",
                        is_returned=True))
    ret.current_status = "Ready for Sale"
    ret.date_sold = None
    db.session.commit()

    # ---- the loss records ----------------------------------------------
    # filed against the two pieces marked above, and processed, so they
    # have actually come off the rail
    db.session.add_all([
        StockAdjustment(adjustment_date=_d(10), adjustment_id="ADJ-00001",
                        item_id=items[-6].id, adjustment_type="Lost",
                        qty_change=-1, reason="Not found during stock count",
                        approved_by="Owner", is_processed=True),
        StockAdjustment(adjustment_date=_d(9), adjustment_id="ADJ-00002",
                        item_id=items[-5].id, adjustment_type="Damaged",
                        qty_change=-1, reason="Zip broken, unsellable",
                        approved_by="Owner", is_processed=True),
    ])
    db.session.commit()

    # ---- expenses over the last three months ---------------------------
    # Rent is the big fixed one; the rest are the small costs he is most
    # likely to forget, which is the whole point of tracking them. Sized so
    # the shop reads as trading profitably rather than losing money.
    expense_defs = []
    for months_back in (2, 1, 0):
        month_start = _first_of_month(
            (TODAY.replace(day=1) - timedelta(days=31 * months_back)))
        expense_defs += [
            (month_start.replace(day=3), "Rent", "Fixed",
             "Shop rent", 12000, "Cash", True),
            (month_start.replace(day=8), "Tea/Food", "Variable",
             "Shop tea and snacks", 850, "Cash", True),
            (month_start.replace(day=12), "Transportation", "Variable",
             "Stock pickup and delivery", 1100, "Cash", True),
            (month_start.replace(day=15), "Packaging", "Variable",
             "Poly bags and tags", 600, "Cash", True),
            (month_start.replace(day=18), "Electricity", "Fixed",
             "Electricity bill", 1150, "UPI", True),
            (month_start.replace(day=22), "Stationery", "Variable",
             "Bill book and receipt books", 350, "Cash", True),
        ]
    expenses = []
    for n, (when, cat, nature, desc, amt, method, paid) in enumerate(
            expense_defs, start=1):
        exp = Expense(expense_date=when, expense_id=f"EXP-{n:05d}",
                      category=cat, nature=nature, description=desc,
                      amount=Decimal(amt), payment_method=method,
                      is_paid=paid)
        expenses.append(exp)
        db.session.add(exp)
    db.session.commit()

    # ---- loans with a payment history ----------------------------------
    db.session.add_all([
        EmiTracker(loan_id="LOAN-00001", loan_name="Business Loan 1",
                   lender="HDFC Bank", loan_type="Business Loan",
                   emi_amount=9500, due_day=7,
                   next_due_date=_next_due(7), outstanding_principal=145000,
                   remaining_emis=18, is_paid=False,
                   paid_date=_d(24), notes="Taken for shop stock"),
        EmiTracker(loan_id="LOAN-00002", loan_name="Gold Loan",
                   lender="Muthoot Fincorp", loan_type="Gold Loan",
                   emi_amount=4200, due_day=15,
                   next_due_date=_next_due(15), outstanding_principal=58000,
                   remaining_emis=14, is_paid=False,
                   paid_date=_d(20), notes="Against gold ornaments"),
    ])
    db.session.commit()

    # ---- the cash ledger -----------------------------------------------
    # Sales, expenses, EMI payments and supplier payments all post to the
    # ledger themselves through the views. The demo data was written
    # directly to the tables, so the same posting is done here - otherwise
    # the cash balance would ignore the trading history and the two sides
    # of the app would not agree with each other.
    counter = [0]
    _post_cash_for(sales, expenses, lots, counter)
    _seed_own_movements(counter)
    db.session.commit()


def _next_txn_id(counter) -> str:
    counter[0] += 1
    return f"TXN-{counter[0]:05d}"


def _post_cash_for(sales, expenses, lots, counter) -> None:
    """The ledger lines the views would have written for this demo data.

    Written directly rather than through post_cash: post_cash assigns its
    own id by reading the table, which autoflushes the pending demo rows
    and collides on the unique constraint. Same shape, same buckets, same
    source links - just minted in one clean numbered series.
    """
    def add(source_type, source_id, when, txn_type, desc, amt, method,
            direction):
        if not amt or float(amt) <= 0:
            return
        if method == "Cash":
            in_key, out_key = "cash_in", "cash_out"
        elif method in ("UPI", "Card"):
            in_key, out_key = "bank_upi_in", "bank_upi_out"
        else:
            return
        row = CashTxn(txn_id=_next_txn_id(counter), date=when,
                      txn_type=txn_type, description=desc,
                      source_type=source_type, source_id=source_id)
        setattr(row, in_key if direction == "in" else out_key,
                Decimal(str(amt)))
        db.session.add(row)

    for s in sales:
        if s.is_returned:
            continue
        add("sale", s.id, s.sale_date, "Sale", f"Sale {s.bill_id}",
            s.final_value, s.payment_method, "in")
    for e in expenses:
        if not e.is_paid:
            continue
        add("expense", e.id, e.expense_date, "Expense",
            f"{e.category} - {e.description}", e.amount, e.payment_method,
            "out")
    for lot in lots:
        if float(lot.paid_amount or 0) > 0:
            # big lots are paid out of the bank, not the drawer; taking
            # Rs 50,000 out of a Rs 12,000 float would leave the shop
            # showing a negative cash balance, which is not what a shop
            # that has been trading looks like
            add("batch", lot.id, lot.purchase_date, "Purchase",
                f"{lot.batch_id} payment", lot.paid_amount, "UPI", "out")
    # EMI payments from the previous two months, so the cash page shows the
    # kind of history the friend is trying to keep straight
    for loan_id, amount, days_ago in (("LOAN-00001", "9500", 24),
                                      ("LOAN-00002", "4200", 20),
                                      ("LOAN-00001", "9500", 54),
                                      ("LOAN-00002", "4200", 50)):
        loan = EmiTracker.query.filter_by(loan_id=loan_id).first()
        if loan is None:
            continue
        add("emi", loan.id, _d(days_ago), "EMI",
            f"{loan_id} {loan.loan_name}", Decimal(amount), "UPI", "out")


def _next_due(due_day: int) -> date:
    """The next occurrence of a day-of-month, in this month or the next."""
    this_month = TODAY.replace(day=min(due_day, 28))
    if this_month >= TODAY:
        return this_month
    nxt = (this_month.replace(day=1) + timedelta(days=32)).replace(day=1)
    return nxt.replace(day=min(due_day, 28))


def _seed_own_movements(counter) -> None:
    """The handful of movements that have no other screen.

    These are exactly the ones the app insists on being typed by hand:
    money in from his own pocket, money back out to himself, transfers
    between drawer and bank, refunds, and income that is not stock.
    """
    def add(**kw):
        db.session.add(CashTxn(
            txn_id=_next_txn_id(counter),
            txn_type=kw.pop("txn_type"),
            description=kw.pop("description"),
            **kw))

    # money put in from his own pocket - not income, which is the whole
    # reason this type exists
    add(date=_d(72), txn_type="Owner Investment",
        description="Own savings put into the shop float",
        cash_in=Decimal("8000"))
    add(date=_d(36), txn_type="Owner Investment",
        description="Own savings to cover the second lot",
        bank_upi_in=Decimal("15000"))
    # taken back out for himself
    add(date=_d(50), txn_type="Owner Withdrawal",
        description="Personal withdrawal", cash_out=Decimal("5000"))
    add(date=_d(22), txn_type="Owner Withdrawal",
        description="Personal withdrawal", bank_upi_out=Decimal("8000"))
    # money moved between the drawer and the bank
    add(date=_d(45), txn_type="Transfer",
        description="Cash into bank", cash_out=Decimal("6000"),
        bank_upi_in=Decimal("6000"))
    # a refund to a customer
    add(date=_d(6), txn_type="Refund",
        description="Refund for returned kurta", cash_out=Decimal("1000"))
    # something that is money in, but not from selling stock
    add(date=_d(30), txn_type="Other Income",
        description="Scrap and old hangers sold", cash_in=Decimal("850"))
