# Business Management System — Database Design

## Overview

The BMS database stores information about users, employees, branches, products, inventory, sales, customers, shifts, expenses, notifications, and system activity.

## Tables

1. users
2. employees
3. branches
4. categories
5. products
6. branch_inventory
7. inventory_transactions
8. sales
9. sale_items
10. customers
11. shifts
12. expenses
13. notifications
14. audit_logs
15. loyalty_transactions
16. guest_loyalty_claims

## 1. users

Stores system accounts and role information.

| Field | Type | Constraints | Description |
|---|---|---|---|
| id | Integer | Primary Key | Unique user identifier |
| username | String | Unique, Not Null | Username used to log in |
| email | String | Unique, Not Null | User email address |
| password_hash | String | Not Null | Hashed user password |
| role | String | Not Null | User role: Admin, Manager, or Employee |
| is_active | Boolean | Not Null | Indicates whether the account is active |
| created_at | DateTime | Not Null | Account creation date and time |
| employee_id | Integer | Foreign Key → employees.id, Nullable, Unique | Optional one-to-one link to the employee profile |

## 2. branches

Stores information about store branches.

| Field | Type | Constraints | Description |
|---|---|---|---|
| id | Integer | Primary Key | Unique branch identifier |
| name | String | Not Null | Branch name |
| address | String | Not Null | Branch address |
| phone | String | | Branch phone number |
| status | String | Not Null | Branch status |

## 3. employees

Stores information about employees.

| Field | Type | Constraints | Description |
|---|---|---|---|
| id | Integer | Primary Key | Unique employee identifier |
| full_name | String | Not Null | Employee full name |
| position | String | Not Null | Employee job position |
| phone | String | | Employee phone number |
| email | String | Unique | Employee email address |
| hire_date | Date | Not Null | Date when the employee was hired |
| branch_id | Integer | Foreign Key → branches.id, Not Null | Branch where the employee works |
| status | String | Not Null | Employee status |

An Employee may remain unlinked from a User. User-to-Employee links are
optional; existing records are not associated automatically.

## 4. categories

Stores product categories.

| Field | Type | Constraints | Description |
|---|---|---|---|
| id | Integer | Primary Key | Unique category identifier |
| name | String | Unique, Not Null | Category name |

## 5. products

Stores information about products.

| Field | Type | Constraints | Description |
|---|---|---|---|
| id | Integer | Primary Key | Unique product identifier |
| name | String | Not Null | Product name |
| sku | String | Unique, Not Null | Unique product SKU |
| category_id | Integer | Foreign Key → categories.id, Not Null | Product category |
| branch_id | Integer | Foreign Key → branches.id, Not Null | Legacy branch assignment retained during the inventory refactor |
| purchase_price | Decimal | Not Null | Product purchase price |
| selling_price | Decimal | Not Null | Product selling price |
| stock_quantity | Integer | Not Null | Legacy stock snapshot retained during the inventory refactor |
| minimum_stock | Integer | Not Null | Legacy minimum-stock snapshot retained during the inventory refactor |

## 6. branch_inventory

Stores each product's stock and minimum-stock threshold for a branch.

| Field | Type | Constraints | Description |
|---|---|---|---|
| id | Integer | Primary Key | Unique branch inventory identifier |
| branch_id | Integer | Foreign Key → branches.id, Not Null | Branch holding the product |
| product_id | Integer | Foreign Key → products.id, Not Null | Product in the branch |
| stock_quantity | Integer | Not Null, Default 0 | Current stock at this branch |
| minimum_stock | Integer | Not Null, Default 0 | Minimum desired stock at this branch |

The database enforces a unique `(branch_id, product_id)` pair. During this
compatibility phase, each product's current legacy branch and stock values are
copied into one branch inventory row; the legacy Product columns remain.
Existing inventory movements are associated with the row for the product's
current legacy branch. The old schema does not prove whether that branch was
also the product's branch when each historical movement occurred.
Active inventory management now reads and updates `branch_inventory`; the
legacy stock and minimum-stock values on `products` are retained for
compatibility and are not synchronized with later branch movements.

## 7. inventory_transactions

Stores the history of product stock movements.

| Field | Type | Constraints | Description |
|---|---|---|---|
| id | Integer | Primary Key | Unique inventory transaction identifier |
| product_id | Integer | Foreign Key → products.id, Not Null | Product affected by the transaction |
| branch_inventory_id | Integer | Foreign Key → branch_inventory.id, Nullable | Branch inventory associated with the movement; nullable for compatibility |
| transaction_type | String | Not Null | Transaction type: stock_in or write_off |
| quantity | Integer | Not Null | Quantity of products moved |
| created_at | DateTime | Not Null | Date and time of the transaction |
| sale_id | Integer | Foreign Key → sales.id, Nullable | Optional sale that caused the inventory movement |

## 8. customers

Stores customer information.

| Field | Type | Constraints | Description |
|---|---|---|---|
| id | Integer | Primary Key | Unique customer identifier |
| name | String | Not Null | Customer name |
| phone | String | Unique, Nullable | Customer phone number; canonicalized for new or changed records |
| email | String | | Customer email address |
| password_hash | String(255) | Nullable | Customer portal password hash; never stores a plaintext password |
| registration_date | Date | Not Null | Customer registration date |

Customer phone is the canonical customer contact and duplicate key. New or
changed phone numbers are validated, reduced to their digits for comparison,
and stored in canonical `+<digits>` form. Existing phone values are not
rewritten. The `uq_customers_phone` unique index protects stored values; the
application also checks digit-normalized values so historical formatting
variants cannot be duplicated by new entries. Existing duplicate phone groups
must be reviewed explicitly before running
`migrations/phase3_customer_phone_unique.py`; the migration refuses conflicts
and does not modify customer, sale, or sale-item rows. Email remains optional
and is not used as a customer identity key. Customer portal credentials live
on the existing customer identity and are
never stored in the staff users table. Existing customer rows without a
password cannot be claimed by self-registration based only on knowing the
phone number; a trusted store-side process must verify ownership before
enabling those accounts. Loyalty activity is recorded in the customer-linked
ledger below.

## 9. sales

Stores completed sales.

| Field | Type | Constraints | Description |
|---|---|---|---|
| id | Integer | Primary Key | Unique sale identifier |
| customer_id | Integer | Foreign Key → customers.id, Nullable | Customer associated with the sale |
| branch_id | Integer | Foreign Key → branches.id, Not Null | Branch where the sale was made |
| total_amount | Decimal | Not Null | Final sale amount |
| discount | Decimal | Nullable | Optional discount applied to the sale |
| payment_method | String | Not Null | Payment method used for the sale |
| created_at | DateTime | Not Null | Date and time of the sale |
| created_by_user_id | Integer | Foreign Key → users.id, Nullable | User who processed the sale |

## 10. sale_items

Stores individual products included in a sale.

| Field | Type | Constraints | Description |
|---|---|---|---|
| id | Integer | Primary Key | Unique sale item identifier |
| sale_id | Integer | Foreign Key → sales.id, Not Null | Sale containing this item |
| product_id | Integer | Foreign Key → products.id, Not Null | Product included in the sale |
| quantity | Integer | Not Null | Quantity sold |
| unit_price | Decimal | Not Null | Product selling price at the time of sale |

## 11. shifts

Stores employee work shifts.

| Field | Type | Constraints | Description |
|---|---|---|---|
| id | Integer | Primary Key | Unique shift identifier |
| employee_id | Integer | Foreign Key → employees.id, Not Null | Employee assigned to the shift |
| date | Date | Not Null | Shift date |
| start_time | Time | Not Null | Shift start time |
| end_time | Time | Not Null | Shift end time |
| status | String | Not Null | Shift status: scheduled, completed, or cancelled |

## 12. expenses

Stores business expense records.

| Field | Type | Constraints | Description |
|---|---|---|---|
| id | Integer | Primary Key | Unique expense identifier |
| branch_id | Integer | Foreign Key → branches.id, Not Null | Branch associated with the expense |
| category | String | Not Null | Expense category |
| amount | Decimal | Not Null | Expense amount |
| description | String | | Expense description |
| date | Date | Not Null | Expense date |

## 13. notifications

Stores important system notifications.

| Field | Type | Constraints | Description |
|---|---|---|---|
| id | Integer | Primary Key | Unique notification identifier |
| user_id | Integer | Foreign Key → users.id, Not Null | User receiving the notification |
| message | String | Not Null | Notification message |
| is_read | Boolean | Not Null | Indicates whether the notification has been read |
| created_at | DateTime | Not Null | Notification creation date and time |

## 14. audit_logs

Stores important actions performed in the system.

| Field | Type | Constraints | Description |
|---|---|---|---|
| id | Integer | Primary Key | Unique audit log identifier |
| user_id | Integer | Foreign Key → users.id, Not Null | User who performed the action |
| action | String | Not Null | Description of the performed action |
| object_type | String | Not Null | Type of object affected by the action |
| object_id | Integer | | Identifier of the affected object |
| created_at | DateTime | Not Null | Date and time when the action occurred |

## 15. loyalty_transactions

Stores the append-only, cross-branch history of customer loyalty points. The
existing `customers` row remains the canonical customer identity; guest sales
remain valid and have no loyalty transactions.

| Field | Type | Constraints | Description |
|---|---|---|---|
| id | Integer | Primary Key | Unique loyalty transaction identifier |
| customer_id | Integer | Foreign Key -> customers.id, Not Null, Restrict on delete | Canonical customer receiving or spending points |
| sale_id | Integer | Foreign Key -> sales.id, Nullable, Restrict on delete | Optional related sale |
| branch_id | Integer | Foreign Key -> branches.id, Nullable, Restrict on delete | Optional branch where the operation occurred |
| transaction_type | String | Not Null, Check: earn, redeem, or reversal | Ledger operation kind |
| points | Integer | Not Null, Nonzero, signed by operation type | Signed whole-point change; earn is positive, redeem negative, reversal may be either |
| operation_id | String | Not Null, Unique, Nonempty | Caller-supplied idempotency key |
| created_at | DateTime | Not Null, Default current timestamp | UTC operation timestamp |

The database prevents more than one earn or redeem for the same customer,
sale, and operation type. A sale may have one redemption and one earning;
the earning is calculated on the amount paid after that redemption, so points
earned by the current purchase are not available for its redemption. An insert
trigger verifies that a linked sale belongs to the same customer and
that a supplied branch matches the sale. Update and delete triggers make the
ledger immutable; corrections are represented by reversal entries with new
operation identifiers. The insert trigger checks the existing signed ledger
sum before accepting any entry, so a balance cannot become negative even when
future concurrent redemption logic attempts to write directly to SQLite.
Current balance is derived from the sum of ledger points and is not duplicated
on `customers`.

## Loyalty Rules

- Award 1% of the actual amount paid, rounded down to whole points.
- One point equals one Kyrgyz som (KGS).
- Redeem no more than 20% of a purchase, rounded down to whole points, and
  never more than the available balance.
- Points are shared across all branches because each balance is customer-wide;
  branch attribution is retained only as optional transaction history.
- Checkout awards positive whole-point earnings for customer-linked sales,
  calculated server-side from the paid sale total using the `Decimal` helper.
  Earnings, redemption, sale, sale items, and inventory updates commit
  atomically. Guest sales remain eligible for the QR claim flow and zero-point
  earnings do not create ledger entries.
- Staff checkout calculates the available balance and 20% subtotal limit on
  the server. The sale's gross `total_amount` is preserved; redeemed points
  are recorded as a negative ledger transaction and the same KGS amount is
  stored in `discount`. Earnings are calculated on the net amount actually
  paid.
- The receipt shows gross subtotal, bonus discount, amount actually paid,
  redeemed and earned points, and the linked customer's current ledger
  balance. Historical sales and ledger entries are not recalculated.

The additive SQLite migration is `migrations/phase4_loyalty_ledger.py`. It
creates and verifies a pre-migration backup, adds only the new ledger table,
indexes, and triggers, and checks that all existing table schemas and rows are
unchanged. It is idempotent and refuses an incompatible pre-existing ledger
schema. Review the target database and retain the backup when running the
migration. Checkout assumes this migration is applied; it does not backfill or
change historical sales.

The additive trigger update is `migrations/phase7_loyalty_redemption.py`.
It replaces only the ledger insert-validation trigger so a single sale may
have its one permitted redemption and one permitted earning. It creates and
verifies a pre-migration backup, validates the existing ledger schema and
constraints, and is idempotent. Run it only against the intended deployment
database after reviewing the backup:

```powershell
.\venv\Scripts\python.exe migrations\phase7_loyalty_redemption.py --database C:\path\to\bms.sqlite
```

This migration is not run automatically by the application and must be applied
before POS redemption is enabled on databases that still have the phase 4
trigger. It does not rewrite any sales, balances, inventory, or existing
guest-claim point snapshots.

Guest QR claims retain their saved `points` snapshot. New guest claims use the
1% earning rule, while existing claims remain claimable for their originally
saved amount.

## 16. guest_loyalty_claims

Stores one pending QR claim for each guest sale. The table contains only a
SHA-256 hash of the opaque, cryptographically random token; the raw token is
held temporarily in an HttpOnly, sale-scoped browser cookie so the staff
receipt can be re-opened without invalidating or re-issuing the claim.

| Field | Type | Constraints | Description |
|---|---|---|---|
| id | Integer | Primary Key | Claim row identifier |
| sale_id | Integer | Foreign Key -> sales.id, Not Null, Unique, Restrict on delete | The single guest sale this claim belongs to |
| branch_id | Integer | Foreign Key -> branches.id, Not Null, Restrict on delete | Branch recorded on the sale |
| token_hash | String(64) | Not Null, Unique | SHA-256 hash of the opaque token; raw token is not stored |
| points | Integer | Not Null, Nonnegative | Potential points calculated from the saved sale amount |
| expires_at | DateTime | Not Null | Claim expiry, seven days by default |
| claimed_at | DateTime | Nullable | Reserved for an authenticated claim in a later stage |
| created_at | DateTime | Not Null, Default current timestamp | Claim creation time |

SQLite triggers allow claim rows only for guest sales at the same branch and
reject claims for a sale that already has loyalty ledger activity. A separate
ledger trigger prevents credit while a claim is pending. The authenticated
customer portal claims an unexpired row and adds exactly one ledger earning
atomically. The claim's saved `points` value is authoritative; changing the
earning rate does not recalculate or alter an existing claim. Opening a QR link
only validates and displays potential points and never credits them.

The additive migration is `migrations/phase5_guest_loyalty_claims.py`. Run it
only against the intended non-live or controlled deployment database after
reviewing its backup, for example:

```powershell
.\venv\Scripts\python.exe migrations\phase5_guest_loyalty_claims.py --database C:\path\to\bms.sqlite
```

Set `BMS_PUBLIC_BASE_URL` (or Flask `PUBLIC_BASE_URL`) to the public HTTPS
origin before issuing guest receipts. Do not use localhost or a loopback IP.
The expiry defaults to seven days and can be changed with
`BMS_LOYALTY_CLAIM_TTL_DAYS` or Flask's `LOYALTY_CLAIM_TTL_DAYS` setting.
For local development on a phone, expose the development server through a
reachable HTTPS tunnel or staging hostname and set this variable to that
origin. QR generation is local and uses the `qrcode` package; no token is sent
to an external image service. The QR carries the token in the URL fragment,
which is removed by the page before a rate-limited validation POST. Public
validation is limited to 20 attempts per minute per client IP per application
process.

## Customer Loyalty Portal

The Russian customer portal is served under `/portal` and is separate from
staff authentication and roles. It uses the existing `customers` row and
phone identity; passwords are hashed with Werkzeug and stored in the nullable
`customers.password_hash` column. Registration creates a customer only when
the normalized phone number is not already present. To avoid account takeover,
a pre-existing POS customer cannot self-register using only the phone number;
store staff must verify ownership before enabling that account. Login clears
and rotates the signed session, uses a distinct `customer_id` key, and has
CSRF-protected forms and bounded per-process IP/phone attempt limits. Configure
a trusted reverse proxy or edge rate limiter for multi-worker deployments.

The dashboard derives the balance and earned/redeemed totals from the
append-only ledger. Customer queries are always scoped to the authenticated
customer. A guest QR must first be validated; the token is then held in a
short-lived Secure, HttpOnly, SameSite cookie scoped to `/portal`. An
authenticated customer must explicitly confirm the claim. Claim consumption,
guest-sale association, and the earn ledger insert run under one SQLite
`BEGIN IMMEDIATE` transaction. A sale already linked to a customer, expired or
consumed claim, mismatched branch/points, or any existing sale ledger entry is
rejected. Opening or validating a QR never grants points. Zero-point claims do
not create ledger rows. Registration, login, and this claim flow do not add
bonus redemption.

The additive customer account migration is
`migrations/phase6_customer_portal.py`. It adds only nullable
`customers.password_hash`, verifies SQLite compatibility, backs up the
database before modification, and preserves existing customer rows. Do not
run it against the live database as part of this development stage. For a
reviewed non-live database copy:

```powershell
.\venv\Scripts\python.exe migrations\phase6_customer_portal.py --database C:\path\to\development-copy.sqlite
```

Point a development server at that same copy with
`$env:BMS_DATABASE_URI = 'sqlite:///C:\path\to\development-copy.sqlite'`.
Use a synthetic/anonymized non-live database; do not copy production data into
a public tunnel environment.

### QR development setup and receipt diagnosis

If a guest receipt says the QR is temporarily unavailable, first check
`BMS_PUBLIC_BASE_URL`. The app intentionally omits the QR when this setting is
empty; a QR must contain a reachable HTTPS claim URL, not localhost or a
loopback address. The cashier must also open BMS through that same HTTPS
hostname so the Secure receipt token cookie is available when the receipt is
reopened.
Customer/staff session cookies are Secure by default outside test mode. For
HTTP-only local development (never production), explicitly set
`BMS_SESSION_COOKIE_SECURE=false`; when testing through the HTTPS tunnel, leave
it enabled.

For no-cost phone testing, use a separate development database and a free
Cloudflare Quick Tunnel (or another trusted HTTPS tunnel). Install/run
`cloudflared` locally, then:

1. Set `BMS_DATABASE_URI` to a non-live development database that has the
   phase 6 migration applied. In PowerShell, start the development server on loopback without the
   interactive debugger:
   `$env:BMS_SESSION_COOKIE_SECURE = 'false'; .\venv\Scripts\python.exe -m flask --app run:app run --host 127.0.0.1 --no-debugger --no-reload`
2. In another PowerShell window run
   `cloudflared tunnel --url http://localhost:5000` and copy its generated
   `https://….trycloudflare.com` hostname.
3. Stop and restart only the development Flask server with
   `$env:BMS_PUBLIC_BASE_URL = 'https://….trycloudflare.com'; $env:BMS_SESSION_COOKIE_SECURE = 'true'`
   set, leaving the tunnel running. Keep both values set for subsequent server
   starts.
4. Open the cashier login and complete a guest sale through the generated
   HTTPS hostname, reopen the receipt there, and scan its QR with a phone.
   The phone and cashier must both use the HTTPS hostname, not the local
   `localhost` address.

The tunnel provider terminates HTTPS and can process development traffic;
use synthetic customer data and never expose a production database or
credentials through a development tunnel. Keep TLS verification enabled.
For a future customer account linkage process, verify phone ownership using a
trusted in-person or verified-contact workflow before setting the existing
customer's password hash; do not merge/create a second customer identity.

### Isolated QR loyalty test seed

The development-only seed script is `scripts/seed_qr_test.py`. It accepts only
the repository's `instance\bms-qr-test.db` path and requires the already
initialized schema. It creates or refreshes one marked test branch, one
manager staff account, one test category, two test products, and their
`branch_inventory` rows with 100 units each. It does not create customers,
sales, or loyalty transactions, and does not run migrations or `db.create_all`.
Re-running it preserves the reserved test identities and restores only the
reserved test products' stock to 100.

Run it explicitly from the repository root:

```powershell
.\venv\Scripts\python.exe .\scripts\seed_qr_test.py --database C:\project1-BMS\instance\bms-qr-test.db
```

The script verifies both the exact allowed file path and the resolved
`BMS_DATABASE_URI` before seeding. The test staff username is
`qr-test-manager`. At execution, set its temporary password at the hidden
`getpass` prompts; do not put a password in the script, source control, command
arguments, or shell history. Leave the prompt empty on later runs to retain the
existing password. The seed command is deliberately not run as part of setup.

# Database Relationships

## ER Diagram

users
  1
  ├──────────── N notifications
  │
  ├──────────── N audit_logs
  ├──────────── N sales (created_by_user_id)
  └──────────── 0..1 employees


branches
  1
  ├──────────── N employees
  ├──────────── N products
  ├──────────── N branch_inventory
  ├──────────── N sales
  ├──────────── N guest_loyalty_claims
  └──────────── N expenses

customers
  1
  ├──────────── N sales
  └──────────── N loyalty_transactions

sales
  1
  └──────────── N loyalty_transactions (optional link)

sales
  1
  └──────────── 0..1 guest_loyalty_claims


categories
  1
  └──────────── N products


employees
  1
  ├──────────── N shifts
  └──────────── 0..1 users


customers
  1
  └──────────── N sales


products
  1
  ├──────────── N branch_inventory
  ├──────────── N inventory_transactions
  │
  └──────────── N sale_items


branch_inventory
  1
  └──────────── N inventory_transactions


sales
  1
  ├──────────── N sale_items
  └──────────── N inventory_transactions