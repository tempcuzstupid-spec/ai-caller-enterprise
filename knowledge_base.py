# COASTAL VANGUARD — KNOWLEDGE BASE
# Complete product catalog, packages, pricing, contraindications.
# Load this into the LLM system prompt for accurate responses.

# ── SHIPPING & PAYMENT ──
SHIPPING = {
    "fulfillment": "Shipped within 24 hours, Monday–Friday",
    "method": "2-day mail",
    "standard_cost": "$35",
    "free_threshold": "$500+",
}

PAYMENT_METHODS = ["CashApp", "Zelle", "Apple Pay", "Visa", "Mastercard", "Amex", "Bank Wire", "Crypto (USDC)"]

CONTACT = {
    "phone": "(386) 843-8160",
    "email": "Management@coastalvanguard.org",
    "website": "coastalvanguard.org",
}

# ── INDIVIDUAL PRODUCTS (top sellers) ──
PRODUCTS = {
    "Semaglutide Pen": {
        "description": "GLP-1 receptor agonist. Lower-dose pens for titration and first-time users.",
        "doses": ["1.5mg", "3.5mg", "5.5mg", "7.5mg", "10mg", "16mg"],
        "pricing": {"1.5mg": "$154", "3.5mg": "$217", "5.5mg": "$245", "7.5mg": "$290", "10mg": "$317", "16mg": "$285"},
        "admin": "Subcutaneous, once weekly",
        "best_for": "First-time GLP-1 users, maintenance, gradual titration",
    },
    "Tirzepatide Pen": {
        "description": "Dual GIP/GLP-1 receptor agonist. Targets two metabolic pathways simultaneously.",
        "doses": ["10mg", "20mg", "30mg", "40mg", "50mg", "60mg"],
        "pricing": {"10mg": "$199", "20mg": "$249", "30mg": "$294", "40mg": "$340", "50mg": "$408", "60mg": "$476"},
        "admin": "Subcutaneous, once weekly",
        "best_for": "GLP-1 plateau, recomposition, dramatic body-composition change",
    },
    "Retatrutide Pen": {
        "description": "Triple agonist (GLP-1/GIP/Glucagon). Clinical trials report up to 22% body weight reduction.",
        "doses": ["1.5mg", "3.5mg", "5.5mg", "8mg", "16mg", "24mg", "32mg", "40mg", "48mg"],
        "pricing": {"1.5mg": "$154", "3.5mg": "$217", "5.5mg": "$245", "8mg": "$249", "16mg": "$285", "24mg": "$385", "32mg": "$476", "40mg": "$566", "48mg": "$680"},
        "admin": "Subcutaneous, once weekly",
        "best_for": "Maximum weight loss, body recomposition, advanced protocols",
    },
    "CJC-1295/Ipamorelin Blend": {
        "description": "The Classic Stack. Synergistic GH pulse maximization. Most popular GH combination.",
        "doses": ["10/10mg Vial"],
        "pricing": {"10/10mg Vial": "$186"},
        "admin": "Subcutaneous, 5 nights on / 2 off, bedtime, fasted",
        "best_for": "Anti-aging, body composition, sleep, recovery, fitness",
    },
    "GHK-Cu": {
        "description": "Copper tripeptide. Tightens skin, improves elasticity, reduces wrinkles, supports hair growth.",
        "doses": ["50mg Vial"],
        "pricing": {"50mg Vial": "$67"},
        "admin": "Subcutaneous, 2-3x weekly",
        "best_for": "Anti-aging, elasticity, hair, wound healing",
    },
    "NAD+": {
        "description": "Restores cellular energy (ATP), activates sirtuins, supports DNA repair. Declines ~50% by age 50.",
        "doses": ["1000mg Vial"],
        "pricing": {"1000mg Vial": "$109"},
        "admin": "Subcutaneous, 2-3x weekly",
        "best_for": "Energy, cognitive function, metabolic optimization",
    },
    "BPC-157": {
        "description": "Body Protection Compound. Accelerates wound healing, repairs tendons/ligaments, reduces gut inflammation.",
        "doses": ["10mg Vial", "20mg Vial"],
        "pricing": {"10mg Vial": "$84", "20mg Vial": "$112"},
        "admin": "Subcutaneous, near injury site or systemic, once daily",
        "best_for": "Injury recovery, gut health, tendon/ligament repair",
    },
    "Wolverine Blend": {
        "description": "BPC-157 + TB-500 at 10/10mg. Synergistic tissue repair formula.",
        "doses": ["10/10mg Vial"],
        "pricing": {"10/10mg Vial": "$137"},
        "admin": "Subcutaneous, 2x weekly",
        "best_for": "Serious athletes, gut inflammation, autoimmune support",
    },
    "Selank": {
        "description": "Russian-developed nootropic. Modulates GABA/serotonin — anxiolytic without sedation.",
        "doses": ["10mg Vial"],
        "pricing": {"10mg Vial": "$81"},
        "admin": "Subcutaneous or intranasal, daily, 250-500mcg",
        "best_for": "Anxiety, stress, focus without stimulants",
    },
    "Semax": {
        "description": "Increases BDNF/NGF — supports neuron growth, focus, memory.",
        "doses": ["10mg Vial"],
        "pricing": {"10mg Vial": "$88"},
        "admin": "Subcutaneous or intranasal, morning, 250-500mcg",
        "best_for": "Enhanced focus, students, creatives, neuroprotection",
    },
    "PT-141": {
        "description": "Libido driver. Acts on CNS melanocortin-4 receptor.",
        "doses": ["10mg Vial"],
        "pricing": {"10mg Vial": "$105"},
        "admin": "Subcutaneous, 30-60min before desired effect, 1-2x weekly max",
        "best_for": "Sexual wellness, libido, vitality",
    },
    "Thymosin Alpha-1 (TA-1)": {
        "description": "Immune-modulating peptide. Enhances T-cell function, balances immune response.",
        "doses": ["10mg Vial"],
        "pricing": {"10mg Vial": "$140"},
        "admin": "Subcutaneous, 2x weekly",
        "best_for": "Compromised immunity, travel, autoimmune support",
    },
    "Melanotan-2": {
        "description": "Stimulates melanin production. Deep, even tan without UV. Appetite-suppressing and libido-enhancing.",
        "doses": ["10mg Vial"],
        "pricing": {"10mg Vial": "$88"},
        "admin": "Subcutaneous, daily loading 2-3 weeks, then 2-3x weekly maintenance",
        "best_for": "Natural tan, fair skin, beach prep, fitness prep",
    },
}

# ── COMPLETE-SOLUTION PACKAGES ──
PACKAGES = {
    "A1 · First Time, Done Right": {
        "group": "Weight Loss",
        "outcome": "8–15% body weight lost in 16 weeks, muscle and skin preserved",
        "who": "First-time GLP-1 user with 25-50lb to lose. Doesn't want skinny-fat with loose skin.",
        "includes": ["Semaglutide 16mg Pen ($285)", "CJC-1295/Ipamorelin 10/10mg ($186)", "GHK-Cu 50mg ($67)", "Pen Tips + Pads ($91)", "Insulin Syringes 100ct ($88)"],
        "total": "$463",
        "protocol": "Semaglutide — once weekly. CJC/Ipa — 5 nights on / 2 off, bedtime, fasted. GHK-Cu — 2-3x weekly on alternate days.",
        "timeline": "Week 1-2: appetite suppression. Week 4-6: 5-8lb lost. Week 8-12: 8-12% lost. Week 16: at goal, muscle preserved, skin supported.",
        "skip_if": "Medullary thyroid carcinoma, MEN-2, active pancreatitis, under 15lb to lose, already on Semaglutide",
    },
    "A2 · Plateau Breaker": {
        "group": "Weight Loss",
        "outcome": "Re-start stalled weight loss by adding GIP receptor axis",
        "who": "Been on Semaglutide 12+ weeks, lost 15-25lb, now stalled.",
        "includes": ["Tirzepatide 20mg Pen ($249)", "Wolverine Blend ($137)", "KPV 10mg ($98)", "Pen Tips + Pads ($91)", "Insulin Syringes 20ct ($35)"],
        "total": "$497",
        "protocol": "Tirzepatide — once weekly. Wolverine — 2x weekly. KPV — 2x daily.",
        "timeline": "Week 3-4: weight loss restarts, 8-12lb over next 8 weeks. Week 6-8: training load up. Week 12: re-evaluate.",
        "skip_if": "Has not been on Semaglutide first. Not willing to train. Severe GI sensitivity.",
    },
    "A3 · Lean & Defined": {
        "group": "Weight Loss",
        "outcome": "Drop body fat while preserving/building lean muscle. Athletic, not just smaller.",
        "who": "Lifts 3+ times/week, 15-30lb fat to lose, terrified of skinny-fat.",
        "includes": ["Tirzepatide 30mg Pen ($294)", "CJC-1295/Ipamorelin ($186)", "MOTS-c 25mg/5ml ($123)", "Pen Tips + Pads ($91)", "Insulin Syringes 100ct ($88)"],
        "total": "$547",
        "protocol": "Tirzepatide — once weekly. CJC/Ipa — 5 nights on / 2 off. MOTS-c — 2-3x weekly, 30-60min pre-workout.",
        "timeline": "Week 2-3: appetite suppression, training energy improves. Week 4-8: 8-12lb lost, lifting numbers preserved. Week 12: visible recomposition.",
        "skip_if": "Non-training users. On metformin — MOTS-c compounds AMPK signal.",
    },
    "A4 · Triple Threat": {
        "group": "Weight Loss",
        "outcome": "15–22% body weight loss in 24 weeks, concurrent muscle preservation. Most aggressive recomp.",
        "who": "Experienced user with 40+lb to lose, prior GLP-1/GIP experience, plateaued on Tirzepatide. Trains 4-5x/week.",
        "includes": ["Retatrutide 5.5mg Pen ($245)", "CJC-1295/Ipamorelin ($186)", "Tesa/Ipa 12mg/3mg ($186)", "Pen Tips + Pads ($91)", "Insulin Syringes 100ct ($88)"],
        "total": "$648",
        "protocol": "Retatrutide — once weekly. CJC/Ipa — Mon-Fri. Tesa/Ipa — Sat-Sun. Training 4-5x/week non-negotiable.",
        "timeline": "Week 1-4: titration. Week 6-12: 8-12% lost. Week 16-20: 15-18% lost. Week 24: at goal.",
        "skip_if": "First-time GLP-1 users. Non-training. Active thyroid, pituitary, or pancreatic concerns.",
    },
    "A5 · Vial-Max Value": {
        "group": "Weight Loss",
        "outcome": "Same dual-agonist result at lowest cost-per-mg, 16+ week supply",
        "who": "Cost-conscious, comfortable with syringe dosing, wants no mid-protocol reorder.",
        "includes": ["2x Tirzepatide 50mg/5ml Vials ($490)", "CJC-1295/Ipamorelin ($186)", "Insulin Syringes 100ct ($88)"],
        "total": "$530",
        "protocol": "Tirzepatide vial — draw weekly dose. Standard titration: 0.25ml → 0.5ml → 0.75ml → 1.0ml. CJC/Ipa — 5 nights on / 2 off.",
        "timeline": "Week 1-4: titration. Week 8-12: 8-12% lost. Week 16: end of first vial. Week 20-32: continued loss.",
        "skip_if": "Not comfortable with syringe measurement. Prior Semaglutide intolerance without Tirzepatide trial.",
    },
    "B1 · Cellular Foundation": {
        "group": "Longevity",
        "outcome": "Restored cellular energy, improved insulin sensitivity, deeper recovery. 'I feel 10 years younger.'",
        "who": "35-55 year old feeling the slowdown. Energy dips, brain fog, longer recovery.",
        "includes": ["NAD+ 1000mg ($109)", "MOTS-c 25mg/5ml ($123)", "Wolverine Blend ($137)", "Insulin Syringes 20ct ($35)"],
        "total": "$320",
        "protocol": "NAD+ — 2-3x weekly. MOTS-c — 2-3x weekly on different days. Wolverine — 2x weekly, 4-6 weeks loading then 1x weekly.",
        "timeline": "Week 1-2: energy lift. Week 3-4: training recovery noticeable. Week 6-8: baseline feels different. Week 12: full effect.",
        "skip_if": "On metformin or AMPK activators. Active malignancy.",
    },
    "B2 · Over-30 Vitality": {
        "group": "Longevity",
        "outcome": "Restored GH pulse, deeper sleep, improved skin, leaner body composition, cognitive clarity.",
        "who": "35-60 year old who wants to feel and look younger without GLP-1 weight-loss journey.",
        "includes": ["CJC-1295/Ipamorelin ($186)", "Tesamorelin 30mg ($175)", "NAD+ 1000mg ($109)", "GHK-Cu 50mg ($67)", "Insulin Syringes 100ct ($88)"],
        "total": "$569",
        "protocol": "CJC/Ipa OR Tesa/Ipa — alternate 5 nights on / 2 off. NAD+ — 2x weekly. GHK-Cu — 2-3x weekly.",
        "timeline": "Week 1-2: deeper sleep. Week 3-4: skin feels different. Week 6-8: waist down 1-2cm. Week 12+: full anti-aging effect.",
        "skip_if": "Active malignancy or pituitary adenoma history. Under 30. Can't commit to 12-week minimum.",
    },
    "B3 · Sleep & Repair": {
        "group": "Longevity",
        "outcome": "Delta-wave sleep restored, gut barrier repaired, chronic inflammation quieted.",
        "who": "40+ with poor sleep, bloating, feeling inflamed, achy joints. Told 'everything looks normal' by PCP.",
        "includes": ["BPC-157 10mg ($84)", "KPV 10mg ($98)", "DSIP 5mg (Premium · Inquire)", "Insulin Syringes 20ct ($35)"],
        "total": "$258",
        "protocol": "BPC-157 — daily or 5-on/2-off. KPV — 2x daily. DSIP — 30min before bed.",
        "timeline": "Week 1: DSIP sleep effect in 3-5 nights. Week 2-3: gut symptoms settle. Week 4-6: joint stiffness reduced. Week 8+: inflammation biomarkers drop.",
        "skip_if": "On chronic immunosuppressants. Diagnosed sleep apnea.",
    },
    "C1 · Iron Body": {
        "group": "Athletic Performance",
        "outcome": "Faster training recovery, tendon/joint resilience, sustained energy. Train 5 days/week without breaking down.",
        "who": "Serious lifter, CrossFit athlete, runner, high-volume trainer. Accumulating wear.",
        "includes": ["Wolverine Blend ($137)", "MOTS-c 25mg/5ml ($123)", "KPV 10mg ($98)", "Insulin Syringes 100ct ($88)"],
        "total": "$348",
        "protocol": "Wolverine — 2x weekly near injury sites. MOTS-c — 2-3x weekly, 30-60min pre-workout. KPV — 2x daily.",
        "timeline": "Week 1-2: MOTS-c recovery effect. Week 2-4: joint/tendon discomfort resolves. Week 4-8: training volume tolerance up. Week 8-12: old injuries 70-90% better.",
        "skip_if": "On metformin or AMPK activators. Active autoimmune flares.",
    },
    "C2 · Comeback": {
        "group": "Athletic Performance",
        "outcome": "Cut recovery time from tendon/ligament/surgical repair in half. Back to full training 4-8 weeks faster.",
        "who": "4-12 weeks out from tendon injury, ligament repair, or surgery.",
        "includes": ["Wolverine Blend ($137)", "Sermorelin 20mg ($123)", "KPV 10mg ($98)", "Insulin Syringes 20ct ($35)"],
        "total": "$331",
        "protocol": "Wolverine — daily for 2 weeks (loading), then 2x weekly, perilesional if possible. Sermorelin — daily, bedtime, fasted. KPV — 2x daily.",
        "timeline": "Week 1-2: KPV controls inflammatory spike. Week 3-4: Wolverine repair signal in full effect. Week 6-8: returning to modified training. Week 10-12: return to full training.",
        "skip_if": "First 48-72 hours after acute injury/surgery. Active malignancy. On immunosuppressants.",
    },
    "D1 · Calm Focus": {
        "group": "Cognitive",
        "outcome": "Calm, clear, sustained focus without stimulants, sedation, or crash.",
        "who": "Knowledge worker, founder, student, creative. Performance ceiling set by stress reactivity and scattered focus.",
        "includes": ["Selank 10mg ($81)", "Semax 10mg ($88)", "Insulin Syringes 20ct ($35)"],
        "total": "$204",
        "protocol": "Selank + Semax — intranasal or subcutaneous, daily, 250-500mcg each. Morning or morning + early afternoon. Cycle: 4 weeks on / 2 weeks off.",
        "timeline": "Week 1: noise floor drops. Week 2-3: focus compounds, longer attention span. Week 4: full effect, operating on different baseline.",
        "skip_if": "On pharmaceutical anxiolytics (SSRIs, benzodiazepines). Expecting caffeine-level stimulation.",
    },
    "D2 · Peak Cognitive": {
        "group": "Cognitive",
        "outcome": "Maximum cognitive output with most advanced nootropic in catalog.",
        "who": "User who ran D1 and wants to go further. Work demands extreme cognitive output.",
        "includes": ["Dihexa 10mg (Premium · Inquire)", "Selank 10mg ($81)", "Insulin Syringes 20ct ($35)"],
        "total": "$204",
        "protocol": "Dihexa — subcutaneous, daily or every other day, specialized dosing. Selank — daily.",
        "timeline": "Week 1-2: Selank calming baseline. Week 2-4: Dihexa neurogenic effect builds. Week 4-8: peak effect, 15-20 IQ-point upgrade in working memory feel.",
        "skip_if": "Without prescriber oversight. On stimulants (Adderall, modafinil).",
    },
    "E1 · Glow Up": {
        "group": "Skin & Beauty",
        "outcome": "Tighter, more elastic, more hydrated skin with reduced fine lines and improved hair quality.",
        "who": "30+ focused on visible skin quality, anti-aging, or hair health.",
        "includes": ["GHK-Cu 50mg ($67)", "GLOW Blend 70mg ($158)", "Insulin Syringes 20ct ($35)"],
        "total": "$260",
        "protocol": "GHK-Cu — 2-3x weekly, 2-3mg per injection. 8 weeks on / 4 weeks off. GLOW — 2-3x weekly on alternate days.",
        "timeline": "Week 2-3: skin feels different. Week 4-6: visible texture improvement. Week 8-12: full effect, fine lines reduced, elasticity improved.",
        "skip_if": "Copper metabolism disorders (Wilson disease). On systemic corticosteroids.",
    },
    "E2 · Tanned & Toned": {
        "group": "Skin & Beauty",
        "outcome": "Lean out for season while building deep, even tan without UV damage.",
        "who": "Preparing for summer event, beach vacation, wedding with 8-12 weeks runway.",
        "includes": ["Semaglutide 8mg Pen ($249)", "Melanotan-2 10mg ($88)", "Pen Tips + Pads 20ct ($23)", "Insulin Syringes 10ct ($18)"],
        "total": "$378",
        "protocol": "Semaglutide — once weekly. MT-II — daily loading 2-3 weeks, then 2-3x weekly maintenance. Inject evening.",
        "timeline": "Week 1-2: Semaglutide appetite suppression, MT-II loading starts. Week 2-3: visible tan develops. Week 6-8: 5-8lb lighter, deep tan. Week 12: at or near event.",
        "skip_if": "History of melanoma or atypical mole syndrome. History of priapism. New to peptides.",
    },
    "F1 · Road Warrior": {
        "group": "Immune & Travel",
        "outcome": "Stay healthy and on-protocol through multi-week international travel or high-exposure work.",
        "who": "International business traveler, conference circuit, front-line healthcare worker.",
        "includes": ["Semaglutide 8mg Pen ($249)", "Thymosin Alpha-1 10mg ($140)", "Pen Tips + Pads 100ct ($91)"],
        "total": "$348",
        "protocol": "Semaglutide — once weekly, same day regardless of time zone. TA-1 — 2x weekly (Mon/Thu), 1.6mg per injection. Start TA-1 2 weeks before travel.",
        "timeline": "Pre-trip (2 weeks): start TA-1. During travel: Semaglutide keeps weight constant, TA-1 supports immune system. Post-trip: most report 'didn't get sick this time.'",
        "skip_if": "1-2 day domestic trips. On immunosuppressants. Active autoimmune flare.",
    },
    "G1 · Reignite": {
        "group": "Sexual Wellness",
        "outcome": "Restored libido, improved sexual performance, energy + confidence.",
        "who": "35-55 year old man or woman whose libido has dropped, energy isn't what it was.",
        "includes": ["PT-141 10mg ($105)", "CJC-1295/Ipamorelin ($186)", "Thymosin Alpha-1 10mg ($140)", "Insulin Syringes 20ct ($35)"],
        "total": "$323",
        "protocol": "CJC/Ipa — 5 nights on / 2 off. TA-1 — 2x weekly. PT-141 — 30-60min before desired effect, 1-2x weekly max. Do not use daily.",
        "timeline": "Week 1-2: CJC/Ipa sleep and energy foundation. PT-141 first use shows acute effect. Week 3-4: baseline energy better, libido more consistent. Week 6-8: full effect.",
        "skip_if": "On immunosuppressants. Cardiovascular conditions. On existing ED medication.",
    },
    "H1 · Triple Entry": {
        "group": "Retatrutide Flagship",
        "outcome": "First-time triple-agonist experience. 6-10% body weight in 12 weeks.",
        "who": "Prior GLP-1 experience ready to escalate. 20-40lb to lose.",
        "includes": ["Retatrutide 1.5mg Pen ($154)", "CJC-1295/Ipamorelin ($186)", "GHK-Cu 50mg ($67)", "Pen Tips + Pads 100ct ($91)"],
        "total": "$496",
        "protocol": "Retatrutide 1.5mg — once weekly, 4 weeks at 1.5mg before titrating up. CJC/Ipa — 5 nights on / 2 off. GHK-Cu — 2-3x weekly.",
        "timeline": "Week 1-4: 1.5mg starter, 2-4% lost. Week 5-8: escalate to 3.5mg, 5-7% lost. Week 9-12: 6-10% lost.",
        "skip_if": "First-time GLP-1 users. Medullary thyroid carcinoma, MEN-2, active pancreatitis.",
    },
    "H2 · Recomp King": {
        "group": "Retatrutide Flagship",
        "outcome": "12-18% body weight lost in 16 weeks with lean-mass preservation. Most aggressive recomp.",
        "who": "Experienced user with 40-80lb to lose, plateaued on Tirzepatide. Trains 3-5x/week.",
        "includes": ["Retatrutide 5.5mg Pen ($245)", "CJC-1295/Ipamorelin ($186)", "Tesa/Ipa 12mg/3mg ($186)", "Pen Tips + Pads 100ct ($91)"],
        "total": "$648",
        "protocol": "Retatrutide 5.5mg — once weekly. CJC/Ipa — Mon-Fri. Tesa/Ipa — Sat-Sun. Training 4-5x/week non-negotiable.",
        "timeline": "Week 1-4: titration. Week 6-10: 8-12% lost. Week 12-16: 12-18% lost, strength maintained.",
        "skip_if": "First-time GLP-1 users. Non-training. Active thyroid, pituitary, or pancreatic concerns.",
    },
    "H3 · CagriSema Ultra": {
        "group": "Retatrutide Flagship",
        "outcome": "Maximum satiety by combining two independent appetite pathways.",
        "who": "Prior Semaglutide experience, stalled, wants second satiety axis without triple-agonist.",
        "includes": ["2x Semaglutide 25mg Vials ($316)", "Cagrilintide 25mg ($245)", "CJC-1295/Ipamorelin ($186)", "Insulin Syringes 100ct ($88)"],
        "total": "$591",
        "protocol": "Semaglutide vial — draw weekly dose. Cagrilintide — once weekly, different day from Semaglutide. CJC/Ipa — 5 nights on / 2 off.",
        "timeline": "Week 1-4: Semaglutide titration, add Cagrilintide at low dose. Week 6-10: dual-satiety kicks in. Week 12-16: 10-15% body weight lost.",
        "skip_if": "First-time GLP-1 users. Not comfortable with syringe dosing. Has not plateaued on Semaglutide yet.",
    },
    "H4 · Value Triple": {
        "group": "Retatrutide Flagship",
        "outcome": "15-22% body weight loss over 24+ weeks at lowest cost-per-mg.",
        "who": "Experienced triple-agonist user with 6+ month timeline. Wants vial cost advantage.",
        "includes": ["2x Retatrutide 50mg/5ml Vials ($546)", "CJC-1295/Ipamorelin ($186)", "NAD+ 1000mg ($109)", "Insulin Syringes 100ct ($88)"],
        "total": "$634",
        "protocol": "Retatrutide vial — draw weekly dose. CJC/Ipa — 5 nights on / 2 off. NAD+ — 2x weekly.",
        "timeline": "Week 1-4: titration. Week 8-16: 10-15% lost. Week 16-24: 15-22% lost. Week 24+: maintenance.",
        "skip_if": "Not comfortable with syringe calculation. First-time triple-agonist users. On metformin.",
    },
    "H5 · The Big Day": {
        "group": "Retatrutide Flagship",
        "outcome": "Look best from every angle at specific event 12 weeks out. Body comp + skin + radiance.",
        "who": "Wedding, milestone vacation, reunion, or photo-shoot 10-14 weeks out.",
        "includes": ["Retatrutide 3.5mg Pen ($217)", "GLOW Blend 70mg ($158)", "GHK-Cu 50mg ($67)", "NAD+ 1000mg ($109)", "Pen Tips + Pads 20ct ($23)", "Insulin Syringes 10ct ($18)"],
        "total": "$544",
        "protocol": "Retatrutide 3.5mg — once weekly, start 12 weeks before event. GLOW — 2-3x weekly. GHK-Cu — 2-3x weekly. NAD+ — 2x weekly. Stop Retatrutide 1-2 weeks before event.",
        "timeline": "Week 1-4: 4-6% lost, skin quality beginning. Week 5-8: 7-10% lost, skin visibly firmer. Week 9-12: 10-14% lost, skin at peak. Event week: leaner, firmer, more radiant.",
        "skip_if": "Under 8 weeks to event. No prior GLP-1 experience.",
    },
}

# ── UNIVERSAL CONTRAINDICATIONS ──
CONTRAINDICATIONS = {
    "GLP-1 / GIP / Glucagon axis": [
        "Medullary thyroid carcinoma (history or family)",
        "MEN-2 (Multiple Endocrine Neoplasia type 2)",
        "Active pancreatitis",
    ],
    "GH axis": [
        "Active malignancy",
        "Pituitary adenoma history",
    ],
    "Tissue repair (BPC-157, TB-500, Wolverine)": [
        "Active malignancy",
    ],
    "Melanotan-2": [
        "Melanoma or atypical mole syndrome history",
        "Priapism history",
    ],
    "GHK-Cu": [
        "Wilson disease or copper metabolism disorders",
    ],
    "Thymosin Alpha-1": [
        "Immunosuppressant use",
        "Active autoimmune flare",
    ],
    "MOTS-c": [
        "Metformin or other AMPK activators",
    ],
}

# ── HOW TO PICK THE RIGHT PACKAGE ──
PACKAGE_SELECTOR = """
STEP 1: Identify the primary outcome.
  - Weight loss? → Group A or H
  - Longevity/energy? → Group B
  - Athletic performance/recovery? → Group C
  - Focus/cognitive? → Group D
  - Skin/beauty? → Group E
  - Immune/travel? → Group F
  - Sexual wellness? → Group G

STEP 2: Match user profile to package.
  - First-time? → A1 or H1
  - Plateaued on Semaglutide? → A2 or H3
  - Training/lifting? → A3, A4, C1, H2
  - Cost-conscious? → A5, H4
  - Event countdown? → H5
  - Over-30 vitality? → B2
  - Poor sleep/inflamed? → B3
  - Post-injury? → C2
  - Knowledge worker? → D1
  - Summer prep? → E2
  - Traveler? → F1

STEP 3: Check contraindications. If any apply, STOP and refer to healthcare provider.
"""
