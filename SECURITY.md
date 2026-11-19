# Security policy

## Reporting a vulnerability

Please do **not** put the details in a public issue. Use GitHub's private vulnerability
reporting for this repository (Security → Report a vulnerability). If that option is not
shown, reach the maintainer privately through the contact details on their GitHub
profile, or open an issue that only asks for a private channel.

Include what you found, how to reproduce it, and what an attacker could do with it.
Never include real API keys, gateway credentials or customer data: use the sandbox and
the built-in simulator.

You can expect an acknowledgement within three working days, and a fix or a plan within
thirty. Please give us that time before publishing.

## Scope

Especially interesting: anything that lets one workspace read or change another's data;
anything that marks an order paid without a matching provider confirmation; recovery of
stored gateway credentials; server-side request forgery through webhook or gateway URLs;
bypass of the callback signature.

Out of scope: vulnerabilities in the payment providers themselves (report those to the
provider), and deployments run by third parties (report those to their operator).

## Supported versions

The latest release receives security fixes.

[docs/security.md](docs/security.md) describes the security model.
