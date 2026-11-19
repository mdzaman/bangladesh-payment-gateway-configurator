# Compliance notes

This is not legal advice. It describes what the software does so you can discuss it
accurately with counsel.

## The legal background, in one paragraph

Under the Payment and Settlement Systems Act 2024, operating a payment system or
providing payment services in Bangladesh needs a Bangladesh Bank licence. Collecting
other people's money into your own account makes you an aggregator. Mobile financial
service terms generally prohibit collecting business payments into personal wallets.

## What this software is

Merchant-side integration software. For every payment:

* the customer pays on the licensed provider's own page;
* the provider settles into the merchant account whose credentials were used;
* this service starts the payment, verifies the result and records it.

It has no account that receives funds, no ledger of balances, no payout or settlement
function, and no way to move money between merchants. It never sees a card number, a
wallet PIN or an OTP.

## How the hosted, multi-tenant form keeps to that

* **Each workspace uses its own merchant accounts.** There is no shared or platform
  merchant account, and no code path that takes a payment on one workspace's credentials
  for another.
* **Live mode needs a written attestation** by a workspace owner with a confirmed email:
  the business holds its own merchant accounts, payments settle directly into them, and
  the workspace will not collect on behalf of others or into personal wallets. The text,
  the legal name given, who accepted, when and from where are stored with the workspace.
* **The operator can require approval** before any workspace goes live
  (`BDPAY_CLOUD_LIVE_MODE=approval`), or run sandbox only (`disabled`), and can suspend
  a workspace at any time. A suspension stops new payments; payments already at a
  provider are still recorded.
* **Published sandbox credentials are refused in live mode.**

Running this as a service for other businesses may still be a regulated activity
depending on what you offer around it (onboarding merchants for providers, handling
their settlement, charging per transaction). Check before you launch.

## The experimental UddoktaPay driver

UddoktaPay-style tools verify payments sent to wallet numbers. Using one to collect
business payments into personal or agent wallets can breach MFS terms and the licensing
rules above. The driver is therefore:

* switched off for the whole deployment unless `BDPAY_CLOUD_ALLOW_EXPERIMENTAL=true`;
* then still unavailable to a workspace until it ticks a compliance statement;
* labelled experimental everywhere it appears.

Leave it off on a deployment you run for others unless you have taken advice.

## Card data

No endpoint accepts, stores or forwards card numbers. Card payments happen on the
aggregator's hosted page. That keeps the service out of scope for storing cardholder
data; your own PCI DSS obligations as a merchant depend on your acquirer and how you
integrate, so confirm them with the aggregator.

## Personal data

The service stores what merchants send with an order (name, phone, email, address) and
the email addresses of dashboard users. Phone numbers are masked in logs and audit
trails. There is no built-in retention schedule: decide one, and delete or anonymise old
orders in the database according to your own policy and any law that applies to you.

## Not supported

Bangla QR (issued by a bank or MFS, no open e-commerce API), NPSB/BEFTN/RTGS (bank
corporate APIs only), and Stripe or PayPal (not available to Bangladesh-domiciled
merchants) are out of scope.
