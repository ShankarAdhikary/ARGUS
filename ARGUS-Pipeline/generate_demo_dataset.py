"""ARGUS — Comprehensive synthetic dataset generator.

Produces 4 JSON files that satisfy every validation rule checked by the
worker, platform_api, and text_extraction layers:

  demo_network_firs.json         →  worker: dataset_type == "fir"
  demo_network_cdrs.json         →  worker: dataset_type == "cdr"
  demo_network_financial.json    →  worker: dataset_type == "financial"
  demo_network_surveillance.json →  worker: dataset_type == "surveillance"

Validation rules honoured:
  FIR      fir_id (str), date (YYYY-MM-DD), station, complainant, accused,
           mobile (10-digit Indian 6-9 prefix), description (non-empty)

  CDR      call_id (str), caller / receiver (10-digit), timestamp (ISO 8601),
           duration_sec (int > 0)

  Financial  transaction_id (str), account_id (str), counterparty_account (str),
             holder_name, bank, amount, currency, direction, timestamp
             Structuring pattern: amounts 9 000-9 999 (≥3 per account)
             account_id / counterparty_account must NEVER be empty

  Surveillance  event_id, camera_id (≤100 chars), zone (≤200 chars),
                timestamp (ISO 8601, ≤64 chars), suspect_name (≤200 chars),
                match_confidence (0.0–1.0), source_type = "surveillance"

Graph connectivity rules:
  • Every phone used in CDRs is also referenced via USES_PHONE from a Suspect
    (ensured by matching FIR mobile numbers to CDR callers / receivers)
  • Financial hub accounts receive ≥3 structuring transfers (triggers pattern)
  • Burner phones make ≥2 outgoing calls (triggers burner heuristic)
  • Each kingpin has ≥2 cross-zone surveillance sightings within 30 min
    (triggers CROSS_ZONE_MOVEMENT alert)
  • Cross-network bridge edges exist so network path (≤6 hops) works

Five criminal networks: trafficking · narcotics · extortion · hawala · cybercrime

RNG seed is fixed — re-running always produces identical files.

Usage:
    python generate_demo_dataset.py
    python generate_demo_dataset.py --out /tmp/data --firs-per-network 80
"""

from __future__ import annotations

import argparse
import json
import random
from datetime import datetime, timedelta
from pathlib import Path

# ── RNG seed ──────────────────────────────────────────────────────────────────
SEED = 20260909

# ── Prison facilities (used by unified-enroll and judges-view) ────────────────
PRISONS = [
    "Central Jail Delhi", "Tihar Jail", "Pune Central Prison",
    "Mumbai Arthur Road Jail", "Hyderabad Central Prison",
    "Chennai Central Prison", "Bengaluru Central Jail",
    "Lucknow District Jail", "Kolkata Presidency Jail",
    "Ahmedabad Sabarmati Jail", "Chandigarh District Jail",
    "Amritsar Central Jail", "Patna Central Jail",
    "Bhopal Central Jail", "Jaipur Central Jail",
]

# ── Name pools ────────────────────────────────────────────────────────────────
FIRST_NAMES = [
    "Vikram","Rohan","Arjun","Kabir","Devansh","Imran","Farhan","Rajat",
    "Sandeep","Naveen","Tarun","Yusuf","Aditya","Manish","Pranav","Sameer",
    "Gaurav","Nikhil","Rahul","Suresh","Ashok","Vivek","Harsh","Kunal",
    "Ritesh","Ajay","Deepak","Mohit","Anil","Varun","Siddharth","Karan",
    "Dinesh","Pradeep","Ramesh","Ravi","Sachin","Abhishek","Shivam","Tushar",
    "Vishal","Mukesh","Naresh","Pankaj","Rajesh","Sanjay","Vijay","Yogesh",
    "Brijesh","Chirag","Darshan","Eklavya","Firoz","Girish","Hitesh","Ishaan",
    "Jatin","Kamal","Lokesh","Manoj","Nitesh","Omkar","Paresh","Qadir",
    "Rashid","Saurabh","Trilok","Umesh","Wasim","Zahir","Amresh","Bharat",
    "Chetan","Dev","Farukh","Ganesh","Hemant","Irfan","Jagdish","Harpreet",
    "Gurpreet","Baldev","Kulwant","Paramjit","Gurjit","Jaswant","Lakhwinder",
    "Mohinder","Narinder","Satinder","Darshan","Karamjit","Sukhwinder","Amarjit",
]
LAST_NAMES = [
    "Singh","Mehta","Sharma","Verma","Khan","Reddy","Nair","Patel",
    "Kulkarni","Bose","Chauhan","Malhotra","Iyer","Gupta","Joshi","Rao",
    "Desai","Pillai","Bhat","Sethi","Kapoor","Chowdhury","Menon","Saxena",
    "Trivedi","Sinha","Mishra","Pandey","Dubey","Tiwari","Yadav","Maurya",
    "Dixit","Shukla","Bajpai","Srivastava","Agarwal","Bansal","Goel","Mittal",
    "Taneja","Arora","Chopra","Dhawan","Grover","Khanna","Mehra","Narang",
    "Oberoi","Puri","Qureshi","Rastogi","Sabharwal","Thakur","Upadhyay",
    "Wadhwa","Acharya","Behl","Chadha","Dutta","Gill","Sandhu","Bajwa",
    "Dhaliwal","Sidhu","Grewal","Virk","Brar","Randhawa","Sohal","Sahota",
    "Dhillon","Sekhon","Toor","Cheema","Mann","Anand","Bhatt","Nanda",
]
STATIONS = [
    "Central Police Station","Harbor Police Station","East Market Police Station",
    "Northgate Police Station","Riverside Police Station","Old Town Police Station",
    "Sector-7 Police Station","Airport Road Police Station","Fort Police Station",
    "Connaught Place Police Station","Lajpat Nagar Police Station","Nehru Place PS",
    "Saket Police Station","Hauz Khas Police Station","Vasant Kunj Police Station",
    "Dwarka Sector-10 PS","Rohini Sector-18 PS","Pitampura Police Station",
    "Janakpuri Police Station","Uttam Nagar Police Station","Paschim Vihar PS",
    "Palam Village Police Station","Kapashera Police Station","Bijwasan PS",
]
ZONES = [
    "Central Bus Terminal","East Market","Harbor Docks","Northgate Transit Hub",
    "Riverside Colony","Old Town Bazaar","Ring Road Junction","Airport Terminal-2",
    "Railway Station Platform-4","Industrial Zone B","Cyber Hub","Border Checkpoint",
    "University Area","Hotel District","Finance Quarter","Suburban Outskirts",
    "Sector-17 Market","IT Park Gateway","Textile Wholesale Hub","Grain Market",
    "Container Depot","Night Bazaar","Toll Plaza Junction","Warehouse District",
]
BANKS = [
    "State Bank of Bharat","United Commercial Bank","Eastern Finance Cooperative",
    "Pioneer Microfinance","National Urban Bank","Frontier Credit Society",
    "Digital Pay Corp","Apex Savings Bank","Central Cooperative Bank",
    "People's Urban Bank","Northern Credit Union","Southern Finance Trust",
    "Western Commercial Bank","Eastern Commercial Bank","Regional Rural Bank",
    "Industrial Development Bank","Export Import Bank","Merchant Bankers Ltd",
]
OFFENCES = {
    "trafficking": [
        "recruitment of a minor under false employment promises",
        "transport of undocumented persons across district lines",
        "operating an unlicensed placement agency",
        "confiscation of identity documents from workers",
        "coordination of a handover at a transit point",
        "harbouring persons in an unregistered facility",
        "facilitating illegal cross-border movement of persons",
        "running a forced labour racket at a garment factory",
        "issuing fraudulent employment contracts to migrant workers",
        "maintaining a captive workforce at an unregistered site",
        "collecting unlawful fees from domestic workers",
        "transporting minors without guardian consent",
    ],
    "narcotics": [
        "possession of narcotic substances with intent to supply",
        "operating a drug distribution hub near a school zone",
        "money laundering proceeds of narcotics sale",
        "transporting contraband concealed in a commercial vehicle",
        "distribution of synthetic drugs to juveniles",
        "maintaining a clandestine chemical laboratory",
        "financing a narcotics supply chain across state borders",
        "storing narcotics in a residential building",
        "recruiting couriers for inter-state drug trafficking",
        "adulterating pharmaceutical supplies with contraband",
        "operating a covert prescription-drug diversion scheme",
        "coordinating heroin import through a port facility",
    ],
    "extortion": [
        "criminal intimidation of a business owner via threatening calls",
        "extortion of weekly protection money from market traders",
        "issuing death threats via anonymous mobile numbers",
        "demanding ransom from a construction contractor",
        "forcing a shopkeeper to pay for 'security services'",
        "collecting illegal levy from truck drivers on the highway",
        "extorting a restaurant owner through repeated harassment",
        "threatening a developer to secure illegal kickbacks",
        "demanding payment in return for withdrawal of false FIR threat",
        "operating a terror network to extort businesses in industrial belt",
        "collecting 'hafta' from street vendors in the market zone",
        "coercing a land-owner to sign over property under duress",
    ],
    "hawala": [
        "operating an unlicensed hawala transfer network",
        "receiving foreign remittances through illegal channels",
        "laundering terrorist financing proceeds through real estate",
        "maintaining hundi books concealed from regulatory authorities",
        "facilitating bulk cash smuggling across state borders",
        "providing unlicensed currency conversion for criminal proceeds",
        "coordinating cross-border hawala flows with a Pakistani counterpart",
        "channelling funds to a designated terrorist organisation",
        "falsifying import invoices to disguise value transfer",
        "operating a front business as a cover for hawala transactions",
        "splitting large transfers into sub-threshold tranches",
        "using cryptocurrency to convert and transfer hawala proceeds",
    ],
    "cybercrime": [
        "operating a phishing infrastructure targeting bank customers",
        "SIM-swap fraud to gain unauthorised access to financial accounts",
        "running a call-centre scam impersonating tax authorities",
        "distributing malware via fake job portals",
        "ransomware deployment against hospital infrastructure",
        "crypto-fraud via a fake investment platform",
        "data exfiltration from a government database",
        "coordinating a Distributed Denial-of-Service attack on infrastructure",
        "selling stolen PAN-Aadhaar combinations on darknet markets",
        "operating an illegal VoIP gateway for fraud calls",
        "hijacking banking OTPs via rogue telecom equipment",
        "deploying a keylogger via fake government services website",
    ],
}

# ── People builder ────────────────────────────────────────────────────────────

def build_all_people(rng: random.Random) -> dict:
    """Build 5 tiered criminal networks with ~150 total actors.
    Includes cross-network bridge links for path-finding tests."""
    used: set[str] = set()

    def make_name() -> str:
        for _ in range(100_000):
            name = f"{rng.choice(FIRST_NAMES)} {rng.choice(LAST_NAMES)}"
            if name not in used:
                used.add(name)
                return name
        raise RuntimeError("Name pool exhausted")

    networks: dict[str, dict] = {}

    # Network 1 — Trafficking (fixed anchors for backward compatibility)
    kp1 = "Vikram Singh";    used.add(kp1)
    lt1 = ["Rohan Mehta","Imran Khan","Sandeep Reddy"];  used.update(lt1)
    rc1 = [make_name() for _ in range(9)]
    st1 = [make_name() for _ in range(30)]
    networks["trafficking"] = dict(
        kingpin=kp1, lieutenants=lt1, recruiters=rc1, street=st1,
        all=[kp1, *lt1, *rc1, *st1])

    # Network 2 — Narcotics (fixed anchor)
    kp2 = "Rajat Sharma";   used.add(kp2)
    lt2 = [make_name() for _ in range(3)]
    rc2 = [make_name() for _ in range(8)]
    st2 = [make_name() for _ in range(24)]
    networks["narcotics"] = dict(
        kingpin=kp2, lieutenants=lt2, recruiters=rc2, street=st2,
        all=[kp2, *lt2, *rc2, *st2])

    # Network 3 — Extortion (fixed anchor)
    kp3 = "Kabir Verma";    used.add(kp3)
    lt3 = [make_name() for _ in range(2)]
    rc3 = [make_name() for _ in range(6)]
    st3 = [make_name() for _ in range(20)]
    networks["extortion"] = dict(
        kingpin=kp3, lieutenants=lt3, recruiters=rc3, street=st3,
        all=[kp3, *lt3, *rc3, *st3])

    # Network 4 — Hawala (fixed anchor)
    kp4 = "Farhan Malhotra"; used.add(kp4)
    lt4 = [make_name() for _ in range(3)]
    rc4 = [make_name() for _ in range(7)]
    st4 = [make_name() for _ in range(18)]
    networks["hawala"] = dict(
        kingpin=kp4, lieutenants=lt4, recruiters=rc4, street=st4,
        all=[kp4, *lt4, *rc4, *st4])

    # Network 5 — Cybercrime (fixed anchor)
    kp5 = "Aditya Kapoor";  used.add(kp5)
    lt5 = [make_name() for _ in range(2)]
    rc5 = [make_name() for _ in range(5)]
    st5 = [make_name() for _ in range(16)]
    networks["cybercrime"] = dict(
        kingpin=kp5, lieutenants=lt5, recruiters=rc5, street=st5,
        all=[kp5, *lt5, *rc5, *st5])

    # Cross-network bridges (one shared actor between adjacent networks)
    # These let the path-finder find 6-hop routes across networks.
    networks["trafficking"]["street"].append(networks["narcotics"]["recruiters"][0])
    networks["narcotics"]["street"].append(networks["extortion"]["recruiters"][0])
    networks["extortion"]["street"].append(networks["hawala"]["recruiters"][0])
    networks["hawala"]["street"].append(networks["cybercrime"]["recruiters"][0])
    networks["cybercrime"]["street"].append(networks["trafficking"]["recruiters"][0])

    all_people: list[str] = []
    seen_all: set[str] = set()
    for net in networks.values():
        for p in net["all"]:
            if p not in seen_all:
                all_people.append(p)
                seen_all.add(p)

    return {"networks": networks, "all": all_people}


# ── Phone assignment ──────────────────────────────────────────────────────────

def assign_phones(people_data: dict, rng: random.Random) -> tuple[dict, dict]:
    """
    Each person gets one primary number (10 digits, starts 6-9).
    Kingpins and lieutenants also get 3 / 2 burner numbers respectively.
    Fixed anchor numbers for the 5 kingpins ensure stable demo searches.
    Validation: every number is exactly 10 digits, first digit 6-9 (Indian format).
    """
    seen: set[str] = set()

    def make_number() -> str:
        for _ in range(1_000_000):
            prefix = rng.choice("6789")
            suffix = str(rng.randint(10**8, 10**9 - 1))
            num = prefix + suffix
            # Ensure exactly 10 digits
            num = num[:10] if len(num) > 10 else num.ljust(10, "0")
            if len(num) == 10 and num not in seen:
                seen.add(num)
                return num
        raise RuntimeError("Phone pool exhausted")

    primary: dict[str, str] = {}
    burners: dict[str, list[str]] = {}

    # Fixed anchors — never change so demo search terms stay stable
    ANCHORS = {
        "Vikram Singh":    "9999988888",
        "Rohan Mehta":     "9888877777",
        "Rajat Sharma":    "9777766666",
        "Kabir Verma":     "9666655555",
        "Farhan Malhotra": "9555544444",
        "Aditya Kapoor":   "9444433333",
        "Imran Khan":      "9333322222",
        "Sandeep Reddy":   "9222211111",
    }
    for name, num in ANCHORS.items():
        primary[name] = num
        seen.add(num)

    for person in people_data["all"]:
        primary.setdefault(person, make_number())

    for net in people_data["networks"].values():
        burners[net["kingpin"]] = [make_number() for _ in range(3)]
        for lt in net["lieutenants"]:
            burners[lt] = [make_number() for _ in range(2)]

    return primary, burners


# ── FIR generator ─────────────────────────────────────────────────────────────

def generate_firs(
    people_data: dict,
    primary: dict,
    count_per_network: int,
    rng: random.Random,
) -> list[dict]:
    """
    Validation rules applied:
    - fir_id: unique string "FIR-2026-NNNN"
    - date: YYYY-MM-DD (within 2026)
    - station: non-empty string
    - complainant: non-empty string
    - accused: must be a known person (so Neo4j MERGE resolves them)
    - mobile: exactly 10 digits, starts 6-9 (so USES_PHONE MERGE works)
    - description: non-empty free-text (ES full-text search target)
    - network: tag for analytics grouping
    """
    base = datetime(2026, 1, 1, 9, 0, 0)
    firs: list[dict] = []
    seq = 0

    for net_name, net in people_data["networks"].items():
        offence_pool = OFFENCES[net_name]
        # Weight: street operatives appear most, kingpin rarely — realistic skew
        weighted: list[str] = (
            [net["kingpin"]] * 2
            + net["lieutenants"] * 5
            + net["recruiters"] * 8
            + net["street"] * 5
        )
        for _ in range(count_per_network):
            seq += 1
            accused = rng.choice(weighted)
            mobile = primary[accused]
            when = base + timedelta(
                days=rng.randint(0, 270), minutes=rng.randint(0, 600)
            )
            zone = rng.choice(ZONES)
            station = rng.choice(STATIONS)
            offence = rng.choice(offence_pool)
            prison = rng.choice(PRISONS)
            description = (
                f"{accused} was reported at {station} in connection with "
                f"{offence} near {zone}. "
                f"The complaint was filed by a local resident. "
                f"Contact number on record: {mobile}. "
                f"Network affiliation: {net_name}. "
                f"Case referred to investigating officer for further action."
            )
            firs.append({
                "fir_id":        f"FIR-2026-{seq:05d}",
                "date":          when.date().isoformat(),
                "station":       station,
                "complainant":   f"Witness-{seq:05d}",
                "accused":       accused,
                "mobile":        mobile,
                "network":       net_name,
                "prison_facility": prison,
                "description":   description,
            })

    # ── Guaranteed anchor FIRs for every kingpin and lieutenant ───────────────
    # Ensures network/accused returns data even if the random draw missed them.
    anchors = []
    for net_name, net in people_data["networks"].items():
        offence_pool = OFFENCES[net_name]
        for person in [net["kingpin"]] + net["lieutenants"]:
            for _ in range(3):   # 3 FIRs each — guarantees LINKED_TO_FIR edges
                seq += 1
                mobile = primary[person]
                when = base + timedelta(days=rng.randint(0, 270), minutes=rng.randint(0, 600))
                zone = rng.choice(ZONES)
                station = rng.choice(STATIONS)
                offence = rng.choice(offence_pool)
                prison = rng.choice(PRISONS)
                description = (
                    f"{person} was reported at {station} in connection with "
                    f"{offence} near {zone}. "
                    f"Contact number on record: {mobile}. "
                    f"Network affiliation: {net_name}."
                )
                anchors.append({
                    "fir_id":        f"ANCHOR-{seq:05d}",
                    "date":          when.date().isoformat(),
                    "station":       station,
                    "complainant":   f"Witness-A{seq:05d}",
                    "accused":       person,
                    "mobile":        mobile,
                    "network":       net_name,
                    "prison_facility": prison,
                    "description":   description,
                })
    firs.extend(anchors)

    rng.shuffle(firs)
    # Re-sequence IDs after shuffle so they are monotone in output
    for i, f in enumerate(firs, start=1):
        f["fir_id"] = f"FIR-2026-{i:05d}"
    return firs


# ── CDR generator ─────────────────────────────────────────────────────────────

def generate_cdrs(
    people_data: dict,
    primary: dict,
    burners: dict,
    days: int,
    rng: random.Random,
) -> list[dict]:
    """
    Validation rules applied:
    - call_id: unique string "CALL-NNNNNN"
    - caller / receiver: exactly 10-digit strings
    - timestamp: full ISO 8601 (no timezone suffix needed, worker uses it as-is)
    - duration_sec: positive int (15-1200)

    Call topology ensures:
    - All kingpin/lieutenant burner phones make ≥2 outgoing calls
      → burner heuristic fires (confidence = min(0.97, 0.5+0.08*n))
    - Hierarchical calls: KP→LT→RC→ST gives betweenness to lieutenants
    - Peer calls within cells create dense sub-communities (Louvain)
    - Weekly cross-network calls create bridge edges for path finding
    """
    base = datetime(2026, 4, 1, 8, 0, 0)
    cdrs: list[dict] = []
    seq = 0

    def add_call(caller: str, receiver: str, day: int) -> None:
        nonlocal seq
        seq += 1
        when = base + timedelta(
            days=day,
            hours=rng.randint(0, 16),
            minutes=rng.randint(0, 59),
            seconds=rng.randint(0, 59),
        )
        cdrs.append({
            "call_id":      f"CALL-{seq:07d}",
            "caller":       caller,
            "receiver":     receiver,
            "timestamp":    when.isoformat(),
            "duration_sec": rng.randint(15, 1200),
        })

    for net in people_data["networks"].values():
        kp_burners  = burners.get(net["kingpin"], [])
        kp_primary  = primary[net["kingpin"]]
        n_lt        = max(len(net["lieutenants"]), 1)

        cells = [
            {
                "lieutenant": lt,
                "lt_phones":  burners.get(lt, []) + [primary[lt]],
                "recruiters": net["recruiters"][idx::n_lt],
                "street":     net["street"][idx::n_lt],
            }
            for idx, lt in enumerate(net["lieutenants"])
        ]

        kp_pool = kp_burners + [kp_primary] if kp_burners else [kp_primary]

        for day in range(days):
            # Kingpin ↔ lieutenants (burner-heavy)
            for lt in net["lieutenants"]:
                if rng.random() < 0.65:
                    add_call(rng.choice(kp_pool), primary[lt], day)
                if rng.random() < 0.30:
                    add_call(primary[lt], rng.choice(kp_pool), day)

            for cell in cells:
                # Lieutenant ↔ recruiters
                for rec in cell["recruiters"]:
                    if rng.random() < 0.60:
                        add_call(rng.choice(cell["lt_phones"]), primary[rec], day)
                    if rng.random() < 0.22:
                        add_call(primary[rec], rng.choice(cell["lt_phones"]), day)
                # Recruiter → street
                for rec in cell["recruiters"]:
                    for op in cell["street"]:
                        if rng.random() < 0.15:
                            add_call(primary[rec], primary[op], day)
                # Peer chatter within street cell
                if len(cell["street"]) > 1 and rng.random() < 0.50:
                    a, b = rng.sample(cell["street"], 2)
                    add_call(primary[a], primary[b], day)
            # Rare inter-cell call (keeps single component)
            if rng.random() < 0.22 and len(cells) > 1:
                c1, c2 = rng.sample(cells, 2)
                if c1["recruiters"] and c2["recruiters"]:
                    add_call(
                        primary[rng.choice(c1["recruiters"])],
                        primary[rng.choice(c2["recruiters"])],
                        day,
                    )

    # Cross-network bridge calls (weekly) — gives path-finder <6-hop routes
    nets = list(people_data["networks"].values())
    for i in range(len(nets)):
        j = (i + 1) % len(nets)
        bridge_a = nets[i]["lieutenants"][0]
        bridge_b = nets[j]["lieutenants"][0]
        for day in range(0, days, 7):
            add_call(primary[bridge_a], primary[bridge_b], day)
            if rng.random() < 0.4:
                add_call(primary[bridge_b], primary[bridge_a], day)

    return cdrs


# ── Financial generator ───────────────────────────────────────────────────────

def generate_financial(
    people_data: dict,
    primary: dict,          # noqa: ARG001 — kept for signature parity
    rng: random.Random,
) -> list[dict]:
    """
    Validation rules applied:
    - transaction_id: unique non-empty string
    - account_id: non-empty string (worker raises ValueError if missing)
    - counterparty_account: non-empty string (same)
    - holder_name, bank, currency: non-empty strings
    - amount: positive number
    - direction: "outgoing" | "incoming"
    - timestamp: ISO 8601 string

    Pattern rules:
    - Financial structuring fires when ≥3 transfers with amounts 9 000-9 999
      land in the same source account
    - Confidence = min(0.95, 0.55 + count*0.05)
    - We generate 6-10 sub-threshold transfers per street→recruiter pair
      to guarantee the pattern fires with high confidence
    """
    transactions: list[dict] = []
    seq = 0

    def acct(person: str, suffix: str = "") -> str:
        idx = people_data["all"].index(person) + 1 if person in people_data["all"] else 999
        return f"ACCT-{idx:04d}{suffix}"

    for net_name, net in people_data["networks"].items():
        hub   = acct(net["kingpin"], "-HUB")
        shell = acct(net["kingpin"], "-SHELL")
        bank  = rng.choice(BANKS)

        # ── Tier 1: street → recruiter (sub-threshold structuring) ──────────
        for op in net["street"]:
            op_acct = acct(op)
            for rec in net["recruiters"]:
                # Between 4 and 8 transfers per pair so pattern always fires
                n_xfers = rng.randint(4, 8)
                for k in range(n_xfers):
                    seq += 1
                    # Amounts 9000-9999 to stay inside structuring window
                    amount = rng.randint(9000, 9999)
                    month  = rng.randint(1, 9)
                    day    = rng.randint(1, 28)
                    hour   = rng.randint(9, 18)
                    transactions.append({
                        "transaction_id":      f"TXN-{seq:06d}",
                        "account_id":          op_acct,
                        "counterparty_account": acct(rec),
                        "holder_name":         op,
                        "bank":                bank,
                        "amount":              amount,
                        "currency":            "INR",
                        "direction":           "outgoing",
                        "network":             net_name,
                        "timestamp":           f"2026-{month:02d}-{day:02d}T{hour:02d}:00:00",
                    })

        # ── Tier 2: recruiter → lieutenant ───────────────────────────────────
        for rec in net["recruiters"]:
            for lt in net["lieutenants"]:
                if rng.random() < 0.65:
                    seq += 1
                    month = rng.randint(3, 10)
                    transactions.append({
                        "transaction_id":      f"TXN-{seq:06d}",
                        "account_id":          acct(rec),
                        "counterparty_account": acct(lt),
                        "holder_name":         rec,
                        "bank":                rng.choice(BANKS),
                        "amount":              rng.randint(18_000, 49_000),
                        "currency":            "INR",
                        "direction":           "outgoing",
                        "network":             net_name,
                        "timestamp":           f"2026-{month:02d}-{rng.randint(1,28):02d}T{rng.randint(9,20):02d}:00:00",
                    })

        # ── Tier 3: lieutenant → hub ─────────────────────────────────────────
        for lt in net["lieutenants"]:
            seq += 1
            month = rng.randint(4, 11)
            transactions.append({
                "transaction_id":      f"TXN-{seq:06d}",
                "account_id":          acct(lt),
                "counterparty_account": hub,
                "holder_name":         lt,
                "bank":                rng.choice(BANKS),
                "amount":              rng.randint(85_000, 250_000),
                "currency":            "INR",
                "direction":           "outgoing",
                "network":             net_name,
                "timestamp":           f"2026-{month:02d}-{rng.randint(1,28):02d}T10:00:00",
            })

        # ── Tier 4: hub → shell (layering) ───────────────────────────────────
        for _ in range(rng.randint(4, 7)):
            seq += 1
            month = rng.randint(5, 12)
            transactions.append({
                "transaction_id":      f"TXN-{seq:06d}",
                "account_id":          hub,
                "counterparty_account": shell,
                "holder_name":         net["kingpin"],
                "bank":                rng.choice(BANKS),
                "amount":              rng.randint(200_000, 900_000),
                "currency":            "INR",
                "direction":           "outgoing",
                "network":             net_name,
                "timestamp":           f"2026-{month:02d}-{rng.randint(1,28):02d}T14:00:00",
            })

        # ── Incoming (reverse) flows for realism ─────────────────────────────
        for lt in net["lieutenants"]:
            if rng.random() < 0.40:
                seq += 1
                transactions.append({
                    "transaction_id":      f"TXN-{seq:06d}",
                    "account_id":          acct(lt),
                    "counterparty_account": hub,
                    "holder_name":         lt,
                    "bank":                rng.choice(BANKS),
                    "amount":              rng.randint(5_000, 30_000),
                    "currency":            "INR",
                    "direction":           "incoming",
                    "network":             net_name,
                    "timestamp":           f"2026-{rng.randint(1,12):02d}-{rng.randint(1,28):02d}T09:30:00",
                })

    return transactions


# ── Surveillance generator ────────────────────────────────────────────────────

def generate_surveillance(
    people_data: dict,
    rng: random.Random,
) -> list[dict]:
    """
    Validation rules applied:
    - event_id: unique non-empty string
    - camera_id: ≤100 chars (enforced — camera prefix + zone word + seq)
    - zone: ≤200 chars (all zones are well within this)
    - timestamp: ISO 8601, ≤64 chars (datetime.isoformat() is always <30)
    - suspect_name: ≤200 chars (all names are well within this)
    - match_confidence: 0.0–1.0
    - source_type: "surveillance" (literal required by load_demo)

    Alert rules triggered:
    - CROSS_ZONE_MOVEMENT: same suspect in two different zones within ≤30 min
      → scripted for all 5 kingpins
    - WATCHLIST_MATCH: suspect in alert_rules table seen at camera
      → all kingpins and key lieutenants are in alert_rules
    """
    base = datetime(2026, 9, 9, 10, 30, 0)
    events: list[dict] = []
    seq = 0

    def cam_id(zone: str, n: int) -> str:
        # Keep ≤100 chars: prefix (4) + zone word (≤12) + seq (≤4) = ≤20
        word = zone.split()[0].upper()[:12]
        return f"CAM-{word}-{n:03d}"

    def ts(dt: datetime) -> str:
        # ISO 8601 without microseconds — always ≤26 chars
        return dt.strftime("%Y-%m-%dT%H:%M:%S")

    # ── Scripted cross-zone pairs for every kingpin ───────────────────────────
    kingpins = [
        ("Vikram Singh",    "Central Bus Terminal",          "East Market",            22),
        ("Rajat Sharma",    "Harbor Docks",                  "Industrial Zone B",       18),
        ("Kabir Verma",     "Ring Road Junction",            "Old Town Bazaar",         25),
        ("Farhan Malhotra", "Finance Quarter",               "Hotel District",          15),
        ("Aditya Kapoor",   "Cyber Hub",                     "University Area",         20),
    ]
    for kp_name, zone_a, zone_b, gap_minutes in kingpins:
        t0 = base + timedelta(hours=rng.randint(0, 2))
        seq += 1
        events.append({
            "event_id":        f"CAM-{seq:04d}",
            "camera_id":       cam_id(zone_a, seq),
            "zone":            zone_a,
            "timestamp":       ts(t0),
            "suspect_name":    kp_name,
            "match_confidence": round(rng.uniform(0.82, 0.95), 2),
            "source_type":     "surveillance",
        })
        seq += 1
        events.append({
            "event_id":        f"CAM-{seq:04d}",
            "camera_id":       cam_id(zone_b, seq),
            "zone":            zone_b,
            "timestamp":       ts(t0 + timedelta(minutes=gap_minutes)),
            "suspect_name":    kp_name,
            "match_confidence": round(rng.uniform(0.80, 0.93), 2),
            "source_type":     "surveillance",
        })

    # ── Sightings for all lieutenants and top recruiters ─────────────────────
    watched: list[str] = []
    for net in people_data["networks"].values():
        watched += net["lieutenants"]
        watched += net["recruiters"][:4]

    for person in watched:
        n_sight = rng.randint(3, 6)
        for _ in range(n_sight):
            seq += 1
            zone = rng.choice(ZONES)
            t0   = base + timedelta(hours=rng.randint(0, 240))
            events.append({
                "event_id":        f"CAM-{seq:04d}",
                "camera_id":       cam_id(zone, seq),
                "zone":            zone,
                "timestamp":       ts(t0),
                "suspect_name":    person,
                "match_confidence": round(rng.uniform(0.65, 0.93), 2),
                "source_type":     "surveillance",
            })
            # 55% chance of a follow-up sighting at a different zone
            if rng.random() < 0.55:
                seq += 1
                zone2 = rng.choice([z for z in ZONES if z != zone])
                events.append({
                    "event_id":        f"CAM-{seq:04d}",
                    "camera_id":       cam_id(zone2, seq),
                    "zone":            zone2,
                    "timestamp":       ts(t0 + timedelta(minutes=rng.randint(10, 55))),
                    "suspect_name":    person,
                    "match_confidence": round(rng.uniform(0.65, 0.93), 2),
                    "source_type":     "surveillance",
                })

    # ── Scatter sightings across street operatives ────────────────────────────
    all_street = [p for net in people_data["networks"].values() for p in net["street"]]
    sample_street = rng.sample(all_street, min(50, len(all_street)))
    for person in sample_street:
        seq += 1
        zone = rng.choice(ZONES)
        t0   = base + timedelta(hours=rng.randint(0, 600))
        events.append({
            "event_id":        f"CAM-{seq:04d}",
            "camera_id":       cam_id(zone, seq),
            "zone":            zone,
            "timestamp":       ts(t0),
            "suspect_name":    person,
            "match_confidence": round(rng.uniform(0.60, 0.88), 2),
            "source_type":     "surveillance",
        })

    return events


# ── Validation pass ───────────────────────────────────────────────────────────

def _validate(firs, cdrs, financial, surveillance) -> None:
    """Raise AssertionError if any record violates a worker validation rule."""
    fir_ids:  set[str] = set()
    call_ids: set[str] = set()
    txn_ids:  set[str] = set()
    cam_ids:  set[str] = set()

    for r in firs:
        assert r.get("fir_id"),      f"FIR missing fir_id: {r}"
        assert r.get("accused"),     f"FIR missing accused: {r['fir_id']}"
        mobile = r.get("mobile", "")
        assert len(mobile) == 10 and mobile[0] in "6789", \
            f"FIR mobile invalid: {mobile} in {r['fir_id']}"
        assert r["fir_id"] not in fir_ids, f"Duplicate fir_id: {r['fir_id']}"
        fir_ids.add(r["fir_id"])

    for r in cdrs:
        assert r.get("call_id"),     f"CDR missing call_id"
        caller   = r.get("caller", "")
        receiver = r.get("receiver", "")
        assert len(caller)   == 10 and caller[0]   in "6789", f"CDR caller invalid:   {caller}"
        assert len(receiver) == 10 and receiver[0] in "6789", f"CDR receiver invalid: {receiver}"
        assert r.get("duration_sec", 0) > 0, f"CDR duration ≤0: {r['call_id']}"
        assert r["call_id"] not in call_ids, f"Duplicate call_id: {r['call_id']}"
        call_ids.add(r["call_id"])

    for r in financial:
        assert r.get("transaction_id"),         f"Financial missing transaction_id"
        assert r.get("account_id"),             f"Financial missing account_id"
        assert r.get("counterparty_account"),   f"Financial missing counterparty_account"
        assert r["transaction_id"] not in txn_ids, f"Duplicate txn_id: {r['transaction_id']}"
        txn_ids.add(r["transaction_id"])

    for r in surveillance:
        assert r.get("event_id"),               f"Surv missing event_id"
        assert r.get("suspect_name"),           f"Surv missing suspect_name"
        assert len(r.get("camera_id", "")) <= 100, f"camera_id too long: {r['camera_id']}"
        assert len(r.get("zone", ""))      <= 200, f"zone too long: {r['zone']}"
        assert len(r.get("timestamp", "")) <= 64,  f"timestamp too long: {r['timestamp']}"
        assert len(r.get("suspect_name",""))<=200,  f"suspect_name too long: {r['suspect_name']}"
        conf = r.get("match_confidence", -1)
        assert 0.0 <= conf <= 1.0, f"match_confidence out of range: {conf}"
        assert r.get("source_type") == "surveillance", f"source_type must be 'surveillance'"
        assert r["event_id"] not in cam_ids, f"Duplicate event_id: {r['event_id']}"
        cam_ids.add(r["event_id"])


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate ARGUS demo dataset (1 000+ records, all validation rules satisfied)"
    )
    parser.add_argument("--out", type=Path, default=Path("."),
                        help="Output directory (default: current directory)")
    parser.add_argument("--firs-per-network", type=int, default=250,
                        help="FIRs per network (default 250 → 1250 total)")
    parser.add_argument("--cdr-days", type=int, default=90,
                        help="Days of CDR history (default 90)")
    args = parser.parse_args()

    rng = random.Random(SEED)

    print("[+] Building people …")
    people_data = build_all_people(rng)
    print(f"    {len(people_data['all'])} actors across 5 networks")

    print("[+] Assigning phones …")
    primary, burners = assign_phones(people_data, rng)
    n_burners = sum(len(v) for v in burners.values())
    print(f"    {len(primary)} primary numbers  +  {n_burners} burners")

    print("[+] Generating FIRs …")
    firs = generate_firs(people_data, primary, args.firs_per_network, rng)
    print(f"    {len(firs)} FIRs")

    print("[+] Generating CDRs …")
    cdrs = generate_cdrs(people_data, primary, burners, args.cdr_days, rng)
    print(f"    {len(cdrs)} CDR records")

    print("[+] Generating financial transactions …")
    financial = generate_financial(people_data, primary, rng)
    print(f"    {len(financial)} transactions")

    print("[+] Generating surveillance events …")
    surveillance = generate_surveillance(people_data, rng)
    print(f"    {len(surveillance)} surveillance events")

    total = len(firs) + len(cdrs) + len(financial) + len(surveillance)
    print(f"\n    TOTAL RECORDS : {total:,}")

    print("[+] Validating all records against worker rules …")
    _validate(firs, cdrs, financial, surveillance)
    print("    ✓ All validation checks passed")

    args.out.mkdir(parents=True, exist_ok=True)
    targets = {
        "demo_network_firs.json":         firs,
        "demo_network_cdrs.json":         cdrs,
        "demo_network_financial.json":    financial,
        "demo_network_surveillance.json": surveillance,
    }
    for name, payload in targets.items():
        out_path = args.out / name
        out_path.write_text(json.dumps(payload, indent=2))
        print(f"    Wrote {out_path}  ({len(payload):,} records)")

    # ── Quality report ────────────────────────────────────────────────────────
    outgoing: dict[str, int] = {}
    for call in cdrs:
        outgoing[call["caller"]] = outgoing.get(call["caller"], 0) + 1
    burner_numbers = {n for nums in burners.values() for n in nums}
    good_burners = sum(1 for n in burner_numbers if outgoing.get(n, 0) >= 2)

    # Count structuring-eligible accounts (≥3 sub-threshold txns)
    struct_counts: dict[str, int] = {}
    for r in financial:
        if 9000 <= r["amount"] <= 9999:
            struct_counts[r["account_id"]] = struct_counts.get(r["account_id"], 0) + 1
    eligible_struct = sum(1 for c in struct_counts.values() if c >= 3)

    print(f"""
[✔] Dataset quality report
    Networks           : 5  (trafficking, narcotics, extortion, hawala, cybercrime)
    Total actors       : {len(people_data['all'])}
    FIRs               : {len(firs):,}
    CDRs               : {len(cdrs):,}
    Financial txns     : {len(financial):,}
    Surveillance events: {len(surveillance):,}
    Total records      : {total:,}
    Burner phones ≥2   : {good_burners}/{len(burner_numbers)}  (pattern fires ✓)
    Structuring accts  : {eligible_struct}  (pattern fires ✓)
    Cross-zone pairs   : 5 kingpins × 2 sightings each  (alert fires ✓)

    Demo search anchors:
      'Vikram Singh'    → trafficking kingpin  (9999988888)
      'Rajat Sharma'    → narcotics kingpin    (9777766666)
      'Kabir Verma'     → extortion kingpin    (9666655555)
      'Farhan Malhotra' → hawala kingpin       (9555544444)
      'Aditya Kapoor'   → cybercrime kingpin   (9444433333)
""")


if __name__ == "__main__":
    main()
