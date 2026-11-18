"""Generate the atomic Obsidian vault (knowledge_vault/) and the 250-question golden dataset.
Deterministic (seeded). Run: python scripts/build_vault.py"""
import json, random, pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
VAULT = ROOT / "knowledge_vault"
VAULT.mkdir(exist_ok=True)
rnd = random.Random(25)

CLASS_ACTION = {
    "A": "place an immediate lot hold and notify the quality lead",
    "B": "open a quality review within 24 hours",
    "C": "log the excursion with no further action",
}
POLICIES = {
    "policy-cold-chain": ("Cold Chain Policy", "operations", 2,
        "Cold chain products must be stored between 2 and 8 degrees Celsius. Any temperature excursion must be recorded within one hour. The required response to an excursion depends on the risk class of the SKU, see [[policy-excursion-response]] and [[policy-lot-hold]]."),
    "policy-excursion-response": ("Excursion Response Policy", "quality", 3,
        "After a temperature excursion, risk class A SKUs require you to " + CLASS_ACTION["A"] + ". Risk class B SKUs require you to " + CLASS_ACTION["B"] + ". Risk class C SKUs require you to " + CLASS_ACTION["C"] + ". See [[policy-lot-hold]] for hold mechanics."),
    "policy-lot-hold": ("Lot Hold Policy", "quality", 3,
        "A lot hold freezes shipment of a lot. Placing a lot hold is a Tier 3 mutation and needs human approval from an authorized reviewer, see [[policy-hitl-approval]]. Lot identifiers use the format LOT-00000 with five digits."),
    "policy-hitl-approval": ("Human Approval Policy", "governance", 3,
        "Tier 3 actions such as lot holds and SKU status changes are never executed automatically. They are written as PENDING_APPROVAL and wait for a reviewer to approve or reject. Reviewers must not approve their own requests."),
    "policy-recall": ("Product Recall Policy", "quality", 3,
        "A recall is initiated by the quality lead after a confirmed safety defect. All lots of the affected SKU are placed on hold, see [[policy-lot-hold]], and customers are notified within 48 hours."),
    "policy-pii": ("Personal Data Policy", "governance", 2,
        "Personal data such as email addresses, phone numbers and social security numbers must be redacted before storage. Raw personal data is never written to logs, caches or audit records. See [[policy-audit-log]]."),
    "policy-audit-log": ("Audit Log Policy", "governance", 2,
        "Every agent action is written to a hash chained audit log. Each entry includes the hash of the previous entry so tampering is detectable. Audit entries are scoped by tenant."),
    "policy-expiry": ("Expiry Handling Policy", "operations", 1,
        "Products within 30 days of expiry are flagged for priority shipping. Expired products must be quarantined, see [[policy-quarantine]]."),
    "policy-quarantine": ("Quarantine Policy", "quality", 2,
        "Quarantined stock is physically segregated and labeled QUARANTINE. Release from quarantine requires a quality review."),
    "policy-supplier-audit": ("Supplier Audit Policy", "procurement", 1,
        "Critical suppliers are audited every 12 months. Non critical suppliers are audited every 24 months."),
    "policy-returns": ("Returns Policy", "sales", 1,
        "Customer returns are accepted within 30 days of delivery when the seal is intact. Returned cold chain products are never restocked."),
    "policy-labeling": ("Labeling Policy", "operations", 1,
        "Every unit carries a label with SKU, lot number and expiry date. Labels for cold chain products also show the storage temperature range."),
}
CATEGORIES = ["vaccine", "insulin", "antibiotic", "diagnostic kit", "saline", "surgical tool", "bandage", "reagent"]
STORAGE = {"vaccine": "2-8C", "insulin": "2-8C", "antibiotic": "15-25C", "diagnostic kit": "2-30C",
           "saline": "15-25C", "surgical tool": "ambient", "bandage": "ambient", "reagent": "2-8C"}

for pid, (title, domain, tier, body) in POLICIES.items():
    (VAULT / f"{pid}.md").write_text(
        f"---\nid: {pid}\ndomain: {domain}\nrisk_tier: {tier}\nversion: 1\n---\n# {title}\n\n{body}\n")

skus = {}
for i in range(1, 41):
    sid = f"SKU-{i:03d}"
    cat = rnd.choice(CATEGORIES)
    cls = rnd.choice("ABC")
    shelf = rnd.choice(range(90, 721, 30))
    skus[sid] = dict(cat=cat, cls=cls, shelf=shelf, storage=STORAGE[cat])
    links = ("[[policy-cold-chain]], " if STORAGE[cat] == "2-8C" else "[[policy-expiry]], ") + "[[policy-excursion-response]], [[policy-labeling]]"
    body = (f"{sid} is a {cat}. Its shelf life is {shelf} days. Its storage temperature is {STORAGE[cat]}. "
            f"Its risk class is {cls}. Related rules: {links}.")
    (VAULT / f"{sid.lower()}.md").write_text(
        f"---\nid: {sid.lower()}\ndomain: catalog\nrisk_tier: {'A B C'.split().index(cls)+1}\nversion: 1\n---\n# {sid}\n\n{body}\n")

# ---- golden dataset (250) ----
G = []
def add(t, q, a, ids): G.append(dict(type=t, question=q, answer=a, relevant_ids=ids))
sk = list(skus)
for sid in sk:                                              # 40 factoid shelf life
    add("factoid", f"What is the shelf life of {sid}?", f"{skus[sid]['shelf']} days", [sid.lower()])
for sid in rnd.sample(sk, 22):                              # 22 factoid storage
    add("factoid", f"What is the storage temperature of {sid}?", skus[sid]["storage"], [sid.lower()])
for _ in range(62):                                         # 62 complex: compare two SKUs
    a, b = rnd.sample(sk, 2)
    while skus[a]["shelf"] == skus[b]["shelf"]:
        a, b = rnd.sample(sk, 2)
    w = a if skus[a]["shelf"] > skus[b]["shelf"] else b
    add("complex", f"Which has a longer shelf life, {a} or {b}?", w, [a.lower(), b.lower()])
for sid in sk + rnd.sample(sk, 10):                            # 50 multi-hop: SKU -> class -> policy
    c = skus[sid]["cls"]
    add("multihop", f"A temperature excursion happened on {sid}. What action is required?",
        CLASS_ACTION[c], [sid.lower(), "policy-excursion-response"])
pol = list(POLICIES)
for i in range(38):                                         # 38 summary
    p = pol[i % len(pol)]
    add("summary", f"Summarize the {POLICIES[p][0]}.", POLICIES[p][3], [p])
NEG = ["What is the price of {s}?", "Who is the CEO of the company?", "What is the weather in Berlin today?",
       "What is the supplier phone number for {s}?", "How many units of {s} were sold last year?",
       "What is the salary of the quality lead?", "Which competitor makes {s}?", "What is the stock price today?"]
for i in range(38):
    add("negative", NEG[i % len(NEG)].format(s=rnd.choice(sk)), "UNANSWERABLE", [])
for i, g in enumerate(G):
    g["id"] = f"q{i+1:03d}"
(ROOT / "eval").mkdir(exist_ok=True)
(ROOT / "eval" / "golden_dataset.json").write_text(json.dumps(G, indent=1))
print(len(G), "questions;", len(list(VAULT.glob('*.md'))), "notes")
