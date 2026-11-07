"""
Title filter: keep technical roles (with full coverage of data / analytics /
AI / ML work), drop non-technical ones.

Three tiers in latentra_scraper.pipelines.validate — hard drops, soft drops
and the technical-role override — evaluated by is_non_it_title(). The
examples here come from the owner's direction and from a review of every
live title on the board.
"""

import pytest
from scrapy.exceptions import DropItem

from latentra_scraper.items import JobItem
from latentra_scraper.pipelines.validate import ValidatePipeline, is_non_it_title
from latentra_scraper.spiders.base_job_spider import BaseJobSpider


def _item(**over):
    base = dict(
        source="greenhouse", source_job_id="1", title="Software Engineer", company="Acme",
        company_slug="acme", department="Eng", seniority="unknown", location="Austin, TX",
        remote_type="unknown", employment_type="unknown", salary_min=None, salary_max=None,
        salary_currency=None, description_html="<p>Build things.</p>", tags=None,
        apply_url="https://x.example/1", posted_at="2026-11-01 00:00:00",
    )
    base.update(over)
    return JobItem(**base)


def _pipeline_drops(title):
    try:
        ValidatePipeline().process_item(_item(title=title), None)
    except DropItem as e:
        return "Non-IT" in str(e)
    return False


# ── 1. Owner's examples ──────────────────────────────────────────────────

# Domain / tool words (ai, cloud, saas, salesforce, "engineering" as a
# department) must not rescue a business role noun.
OWNER_DROPPED = [
    "Account Executive, AI", "Enterprise Account Executive - Cloud", "Recruiter, Engineering",
    "Technical Recruiter", "Marketing Manager, AI Products", "Sales Manager - SaaS",
    "Salesforce Account Executive", "Customer Success Manager, Data Platform",
]

# Genuinely technical roles that mention sales / marketing / finance stay.
OWNER_KEPT = [
    "Sales Engineer", "Solutions Engineer", "Marketing Data Analyst", "Marketing Analytics Engineer",
    "Salesforce Developer", "Salesforce Administrator", "Revenue Operations Analyst",
    "Finance Systems Analyst", "Financial Data Analyst", "Technical Account Manager",
    "Product Manager", "Program Manager, Technical",
]


# ── 2. Every data / AI role family is kept ───────────────────────────────

DATA_AI_FAMILIES = [
    "Data Engineer", "Data Analyst", "Business Analyst", "BI Developer", "BI Analyst",
    "Business Intelligence Analyst", "Analytics Engineer", "Data Scientist",
    "Machine Learning Engineer", "ML Engineer", "AI Engineer", "LLM Engineer", "GenAI Engineer",
    "Generative AI Engineer", "NLP Engineer", "Computer Vision Engineer", "MLOps Engineer",
    "DataOps Engineer", "Data Ops Engineer", "Data Architect", "AI Architect", "Data Platform Engineer",
    "Data Governance Lead", "Data Quality Analyst", "Data Steward", "Database Administrator", "DBA",
    "ETL Developer", "Prompt Engineer", "AI Researcher", "Research Scientist", "Applied Scientist",
    "Quant Developer", "Quantitative Analyst", "Quantitative Researcher", "Statistical Programmer",
    "Biostatistician", "Decision Scientist", "Data Science Manager", "Head of Data", "Head of Analytics",
    "Director, Data Engineering", "VP, Data Science", "Chief Data Officer", "Analytics Manager",
    "Insights Analyst", "Reporting Analyst", "Product Analyst", "Pricing Analyst",
    "Operations Research Analyst", "Fraud Analyst", "Risk Analyst", "Credit Risk Analyst",
    "Data Annotator", "AI Trainer",
]

# ...including with a business-function qualifier after the role.
DATA_AI_WITH_BUSINESS_QUALIFIER = [
    "Data Engineer, Marketing", "Data Analyst, Finance", "Business Analyst, HR", "BI Developer - Sales",
    "Analytics Engineer, Growth", "Data Scientist, Clinical", "Machine Learning Engineer, Ads",
    "AI Engineer - Legal", "Data Architect, Supply Chain", "Head of Data, Payroll",
    "Director, Marketing Analytics", "Manager, Data Engineering", "Senior Manager, Clinical Data Systems",
    "Senior Analyst, Marketing Science", "Analyst, Data Platform", "Analyst II, Marketing Analytics",
    "Data Analyst, Nursing Operations", "Data Science Manager, Sales", "Clinical Data Manager",
    "Statistical Programmer, Oncology", "Director, Statistical Programming - Immunology",
    "Associate Director, Statistics", "People Analytics Manager", "HR Data Analyst",
    "Sales Operations Analyst", "Marketing Operations Analyst", "Supply Chain Analyst",
    "Operations Analyst", "Security Operations Analyst", "Workforce Analyst",
    "Delivery Consultant - AI/ML, Professional Services - AWS Industries",
    "Delivery Consultant - Data , Professional Services, AWS Industries",
    "Associate Consultant, Generative AI", "SDE II, ML Infra Services, Annapurna Labs",
    "Applied Scientist - Trust and Safety (Multimodal Foundation Model) - Global Frontier Tech Recruitment Program",
    "AI Solutions Engineer, Talent Acquisition", "SoC Machine Learning Design Engineer",
    "Strategist, Agent Development - Retail", "Vice President, AI Risk", "AI Transformation Lead",
    "Quantitative Trading & Research – Securities Services - Associate",
    "Immunology Data Scientist", "Computational Biologist (ML)", "Bioinformatics Scientist",
    "Scientist - AI", "Senior Scientist, Machine Learning",
]


# ── 3. General software / IT roles stay (the board is not data-only) ─────

TECH_KEPT = [
    "Software Engineer", "Senior Backend Engineer", "Full Stack Developer", "iOS Developer",
    "Site Reliability Engineer", "DevOps Engineer", "Cloud Architect", "Solutions Architect",
    "Network Engineer", "Systems Administrator", "IT Support Specialist", "Help Desk Technician",
    "IT Technician, Office Manager Support", "IT Site Operations Support Tech", "Manager, IT Internal Audit",
    "IT Project Manager", "Technical Project Manager", "Project Manager, IT", "Technical Program Manager",
    "Engineering Manager, Payroll", "Director, Engineering", "VP, Technology", "Head of Platform",
    "QA Engineer", "SDET", "Scrum Master", "Technical Writer", "Developer Advocate",
    "Product Designer", "Product Designer, Payroll", "UX Designer, HR Tools", "Visual Designer",
    "Content Designer", "Game Designer", "UX Researcher", "Design Systems Lead, Brand",
    "Security Engineer", "Information System Security Officer (ISSO)", "Cyber Security Officer",
    "Penetration Tester", "Cybersecurity Specialist", "SOC Analyst",
    "Data Center Technician", "Data Center Facilities Technician", "Network Cabling Technician",
    "End User and Systems (AV/VTC) Technician", "Server Engineer", "Software Engineer - 2nd Shift",
    "Technical Customer Support Specialist", "Technical Support Engineer", "Implementation Specialist",
    "Workday Analyst", "HRIS Analyst (Workday)", "SAP Functional Consultant", "ERP Analyst",
    "Oracle Fusion Cloud HCM Functional", "Epic Analyst", "CAD Administrator",
    "Siemens Teamcenter Administrator", "System Development Manager, ADC Monitoring & Observability",
    "Staff SEIT Lead", "Principal PM-T, Growth, Outbound Communications", "Sr. PMT, AWS EC2",
    "People Products Manager- Workday", "Legal Technology Support Administrator I - IT Team - City Attorney's Office",
    "Senior FPGA & Firmware Engineer - SDR", "GEOINT Sensor Support Engineer", "CNC Prototyping Engineer",
    "Sr Prncpl Eng Supplier Quality", "HiL V&V Manager", "Quantum Physicist, Integrated Photonics",
    "Embedded Software Engineer", "Firmware Engineer", "Hardware Engineer", "Mechanical Engineer",
    "Principal / Sr Principal Engineer - Control Account Manager", "Security Tech Operations Manager",
    "Deployment Strategist", "Forward Deployed Engineer", "Member of Technical Staff",
    "Software Engineering Intern", "Intern", "Engineer", "Design Verification",
    "Technology Consultant", "Senior Delivery Consultant - Security, WWPS Professional Services",
    "Sr. Manager, Solution Consulting (OT / Cyber-Physical Systems)", "Consultant (Technical)",
    "Business Systems Analyst", "Finance Systems Manager", "Marketing Technology Manager, HR",
    "Recruiting Platform Engineer", "Payroll Systems Engineer", "Accounting Data & Technology Manager",
    "Senior Platform Manager, Data Products, Finance Accounting", "Data Center Operations, Procurement",
    "Software Engineer, Kitchen Display Systems", "Cookie Platform Engineer",
    "Senior Director, Product Management", "Director, Product", "Senior Product Manager - Tech",
    "Program Manager", "Manager Programs 2", "Engineering Coordinator", "Release Manager",
]


# ── 4. Non-technical roles dropped (owner: "drop the non technical roles") ─

NON_TECH_DROPPED = [
    # Sales / account management / partnerships
    "Account Director", "Director of Sales", "Director of Sales, AI & Data Cloud",
    "Senior Solutions Sales Executive - Data & Analytics", "Cybersecurity Sales Specialist",
    "AI Sales Specialist", "Strategic Account Manager – AI Infrastructure Sales", "Territory Manager",
    "Dealer Success Manager", "Business Development Representative", "Partnerships Manager",
    "Head of Partnerships", "Senior Cloud Alliances Manager", "Client Manager - Insights & Analytics",
    "Business Relationship Manager I - Officer", "Private Client Banker", "Relationship Manager",
    "Insurance Agent", "Customer Experience Manager", "Customer Service Rep", "Product Support Specialist",
    "Chat & Community Support Specialist",
    # Marketing / media / creative
    "Part Time Brand Ambassador", "SEO Specialist", "Paid Ads Specialist (Google/Meta)",
    "Content Creator", "Newsletter Writer", "Short-Form Video Editor", "Executive Producer", "News Anchor",
    "Multimedia Journalist", "Graphic Designer", "Motion Designer", "Brand Designer, Creative Studio",
    "Instructional Designer", "Instructional System Design", "Experiential Designer", "Art Director",
    "Director, Analyst Relations", "Public Relations Vice President, Technology",
    "Partner Marketing Manager, Technology Partners & ISVs", "Head of Marketing & Growth",
    # Operations / project / generic management / admin
    "Operations Manager", "Business Operations Manager", "Strategy & Operations Lead",
    "Sales Operations Manager", "Revenue Operations Manager", "Program Operations Manager",
    "Project Manager", "Senior Project Manager", "Construction Project Manager", "Project Coordinator",
    "Program Coordinator", "Operations Coordinator", "Chief of Staff", "Executive Assistant, Engineering",
    "Office Manager", "Assistant Manager", "General Manager", "Engagement Manager", "Senior Consultant",
    "Management Consultant", "Team Lead, Proposal Support (Casual)", "Senior Specialist, Client Processing",
    "Procurement Operations Business Partner, Technology", "Data Entry Clerk",
    # Recruiting / HR
    "Director, Technical Recruiting", "Engineering Recruiter", "Data Analyst Recruiter",
    "Software Engineer Recruiter", "Technical Sourcer", "Talent Acquisition Partner, Engineering",
    "HR Business Partner, Engineering", "Head of People Operations", "People Operations Manager",
    "Payroll Specialist", "Benefits Analyst", "Compensation Analyst", "Recruiting Coordinator",
    # Finance / accounting / banking
    "Financial Analyst", "Senior Financial Analyst, AI Transformation", "Analyst, Finance",
    "Senior Tax Accountant", "Staff Accountant", "Strategic Finance Analyst", "Liquidity Controller Analyst",
    "2027 BNY Analyst Program - Office of the COO", "Credit Analyst", "Investment Analyst",
    "Program Cost and Schedule Control Analyst", "Senior Manager, Commercial Finance",
    "Director, Product Finance (Devices)", "Underwriter", "Jr. Underwriter", "Junior FX Trader",
    # Legal / compliance / policy
    "Law Clerk I", "Litigation Associate", "Senior Counsel, Patent Prosecution", "Commercial Counsel, GTM",
    "Legal Operations Manager", "Analyst II - Legal Operations", "Compliance Analyst",
    "Manager, State Government Affairs - Northeast", "Paralegal",
    # Clinical / medical / life-science lab
    "Clinical Research Coordinator", "Medical Director, Oncology", "Patient Access Representative",
    "MSL/Senior MSL, Parkinson's Disease (Great Lakes)", "Medical Laboratory Scientist", "Senior Scientist",
    "Scientist I", "ELISA Method Development and Validation Research Scientist I", "Lab Analyst",
    "Lab Tech I - Microbiology", "Laboratory Technician", "CT Technologist - Evenings",
    "Cardiac Catheterization Technologist", "Neurosurgeon Resident", "Behavior Analyst", "Radiologist",
    "Personal Care Assistant", "Pharmacist",
    # Trades / manufacturing / logistics / technicians (non-IT)
    "Field Service Technician", "Diesel Technician", "Manufacturing Technician", "Test Technician",
    "Machinist", "CNC Machinist", "CNC Programmer", "MACH TOOL OPR (CNC)", "Production Supervisor",
    "Production Planner", "Demand Planner", "Material Planner", "Supply Chain Manager",
    "Logistics Specialist", "Warehouse Supervisor", "Buyer", "Quality Control Inspector",
    "Electrical Designer", "Civil Designer", "CAD Designer", "Senior Mechanical Designer",
    "Forklift Operator", "Delivery Expert", "Delivery Associate", "Part-Time Driver (Manheim)",
    "Vehicle Photographer I (Manheim)", "Electrician", "Landscaper", "Landscape Architect",
    "Tool and Die Maker", "UAS Pilot", "Meteorologist", "Staff Geologist",
    # Retail / hospitality / security guard
    "Store Manager", "Cashier", "Line Cook", "Security Officer", "Facility Security Officer",
    "Security Monitor", "Table Games Supervisor", "Surveillance Agent",
    # Education / military ops
    "Research Assistant", "Assistant Professor", "Teacher", "Course Coordinator", "Training Specialist",
    "Intelligence Analyst", "INTEL ANALYST", "Mid All Source Analyst", "Psychological Operations Planner",
    "Entry Level Background Investigator (Top Secret)",
]


# ── 2b. "Basically all data works": analytics / data-* keep a title whatever
#        management noun heads it, and beat a hard match outside the head.

STRONG_DATA_KEPT = [
    "Manager Analytics and Data Integrity", "Manager, Analytics and Market Research",
    "Manager, Analytics and Market Research – Eye Care",
    "Associate Director, Analytics and Performance Excellence (APEX) Specialty PCS",
    "Associate Director, Global Aesthetics Business Development Forecasting & Analytics",
    "Data Mining Specialist (Payment Integrity)", "Senior Manager - Data Mining & Exploratory Intelligence",
    "Auditor II - Data Mining", "Strategic Analytics [Multiple Positions Available]",
    "AI Analytics Strategy And Execution-Senior Associate",
    "Finance Decision Optimization Team - Analytics Solutions Senior Associate",
    "Global Banking & Markets Operations-Salt Lake City-Associate-Strategic Analytics and Configuration",
    "Early Careers: Corporate Risk and Broking (CRB)- Internship - Risk & Analytics Enterprise Risk Consulting - New York - 2027",
    "Retail Finance Program Manager, Analytics & Reporting", "Data Platform Product Delivery Associate Senior",
    "Vice President, Data Delivery and Channels", "Senior Vice President, Data Transformation & Product Strategy",
    "Senior Specialist, Data Transfer Integration & Quality",
    "Senior Engagement Manager- DE & Analytics Manufacturing Expert", "Data Protection Specialist",
    "Data Protection Associate Director (SaaS, Web, Collaboration Security)", "Manager, Data Posture Monitoring",
    "AppleCare Data Privacy Project Manager", "2027 BNY Analyst Program - Engineering (Developer) - New York, NY",
    "2027 BNY Analyst Program - Engineering (Developer) - Pittsburgh, PA", "Web Marketing Intern - Front-end Developer",
    "Electromagnetics (EM) and Packaging Design Engineer",
    "Director, IS&T Business Process Reengineering - Supply Chain Planning and Optimization Platforms",
    "Senior Specialist, Paralegal/Data Analyst - Commercial Law Group", "Evaluation Science Lead",
    "Risk & Compliance SME (AI Focus)", "Director of Data Services", "Data Loss Prevention Manager",
    "Analytics Business Partner", "Supervisor, Analytics Implementation", "Vice President, Mission Data Delivery",
    "Senior Electrical Engineer- SDR Hardware", "Sales Development Engineer", "Global Head of Customer Success Engineering",
    "EVMS Tooling Engineer level 2/3", "Sr. Cost Control Engineer", "Data Acquisition Lead", "Data Risk Manager",
    "Assistant Editor, Data Journalism", "Director, CAD & Technical Data Conversion",
]

# ...but a hard role noun at the head, or one that is unambiguous anywhere,
# still wins; data clerks and buildings are not data work.
STRONG_DATA_STILL_DROPPED = [
    "Product Marketing, Data Products", "Client Manager - Insights & Analytics",
    "Federal Data and Analytics Sr Sales Executive", "Technical Recruiter, Software - Air Dominance and Strike",
    "HR Business Partner– Product and Engineering", "Data Center Construction Manager",
    "Lead Principal Data Center Construction Manager", "Data Entry Specialist", "Data Entry Operator IV",
    "Data Review Associate", "GMP Data Review Associate", "Laboratory Data Review Associate",
    "Instructional Designer – Data Center", "Customer Success Manager, Data Platform",
    "Account Executive - Data Engineering", "Senior Solutions Sales Executive - Data & Analytics",
    "Principal Associate, Recruiter- Staff & Strategy Professional TA (Data Science & Data Analysis)",
    "Manager, Senior Technical Recruiter - AI / ML & Data Science (IC)",
    "Legal, Cyber & Artificial Intelligence Attorney , Vice President , New York/Dallas",
    "Director, People Partners - Product, Design & Engineering", "Director, HR Business Partner - Product Engineering",
    "Director, Business Development - Technology and Data Product Sales", "Financial Analyst, Data Center",
    "Marketing Manager, Data Engineering", "2027 BNY Analyst Program - Office of the COO (Houston, TX)",
    "Supply Chain Program Manager - Rack Integration", "Sr. Construction Program Manager | Data Center Sector",
    "Production Program Manager, Space", "Logistics Program Manager, Advanced Effects",
    "Legal Program Manager, Compute & Infrastructure", "Principal Partner Program Manager - Resellers",
    "Field Data Collection Specialist", "Data Collection Operator - AI Robotics (Swing Shift)",
    "Manager, Data Partnerships", "Senior Technician Data Recording 3rd Shift (Onsite)",
    "Senior Manager, Senior Counsel - Enterprise Data and Cyber", "Business Development Leader - Data Centers",
]


class TestOwnerExamples:
    @pytest.mark.parametrize("title", OWNER_DROPPED)
    def test_business_roles_with_tech_words_drop(self, title):
        assert is_non_it_title(title), title

    @pytest.mark.parametrize("title", OWNER_KEPT)
    def test_technical_roles_mentioning_business_functions_stay(self, title):
        assert not is_non_it_title(title), title


class TestDataAndAiFamiliesKept:
    @pytest.mark.parametrize("title", DATA_AI_FAMILIES)
    def test_family(self, title):
        assert not is_non_it_title(title), title

    @pytest.mark.parametrize("title", DATA_AI_WITH_BUSINESS_QUALIFIER)
    def test_family_with_business_qualifier(self, title):
        assert not is_non_it_title(title), title

    @pytest.mark.parametrize("prefix", ["Senior ", "Staff ", "Lead ", "Principal ", "Junior ", "Sr. "])
    @pytest.mark.parametrize("title", ["Data Engineer", "Data Analyst", "Machine Learning Engineer", "AI Engineer"])
    def test_seniority_prefixes(self, prefix, title):
        assert not is_non_it_title(prefix + title)


class TestStrongDataSignal:
    @pytest.mark.parametrize("title", STRONG_DATA_KEPT)
    def test_data_work_is_kept_whatever_the_management_noun(self, title):
        assert not is_non_it_title(title), title

    @pytest.mark.parametrize("title", STRONG_DATA_STILL_DROPPED)
    def test_role_noun_at_head_or_unambiguous_anywhere_still_drops(self, title):
        assert is_non_it_title(title), title


class TestGeneralTechKept:
    @pytest.mark.parametrize("title", TECH_KEPT)
    def test_kept(self, title):
        assert not is_non_it_title(title), title


class TestNonTechDropped:
    @pytest.mark.parametrize("title", NON_TECH_DROPPED)
    def test_dropped(self, title):
        assert is_non_it_title(title), title


class TestStructure:
    """Head-first evaluation and regex pitfalls."""

    def test_department_in_tail_cannot_sink_technical_head(self):
        assert not is_non_it_title("Software Engineer, Recruiting")
        assert not is_non_it_title("Data Scientist - Sales Compensation")
        assert not is_non_it_title("Senior Software Engineer - Account Executive Tools")

    def test_role_in_head_decides_even_with_tech_tail(self):
        assert is_non_it_title("Recruiter - Data Science")
        assert is_non_it_title("Account Executive - Data Engineering")
        assert is_non_it_title("Financial Analyst, Data Center")
        assert is_non_it_title("Marketing Manager, Data Engineering")

    def test_bare_analyst_head_defers_to_the_tail(self):
        assert is_non_it_title("Analyst, Finance")
        assert is_non_it_title("Senior Analyst - Accounting")
        assert not is_non_it_title("Analyst, Data Platform")
        assert not is_non_it_title("Analyst, Marketing Analytics")

    def test_whole_word_matching(self):
        assert not is_non_it_title("Cookie Platform Engineer")          # cook
        assert not is_non_it_title("Salesforce Developer")              # sales
        assert not is_non_it_title("Product Designer")                  # product design(er)
        assert not is_non_it_title("SoC Machine Learning Design Engineer")  # learning design
        assert not is_non_it_title("Hosting Engineer")                  # host
        assert is_non_it_title("Radiologist")                           # prefix stem needs \\w*
        assert is_non_it_title("Landscaper")

    def test_underscore_separated_titles(self):
        assert is_non_it_title("2nd Shift_ Finishing Technician_ Onsite")

    def test_empty_and_non_string_titles_are_not_dropped_here(self):
        assert not is_non_it_title("")
        assert not is_non_it_title("   ")
        assert not is_non_it_title(None)

    def test_unmatched_titles_pass(self):
        assert not is_non_it_title("General Application")
        assert not is_non_it_title("Associate")


class TestPipelineAndSpiderUseTheSameRule:
    @pytest.mark.parametrize("title", OWNER_DROPPED + ["Project Manager", "Financial Analyst"])
    def test_pipeline_drops(self, title):
        assert _pipeline_drops(title), title

    @pytest.mark.parametrize("title", OWNER_KEPT + ["Data Engineer, Marketing"])
    def test_pipeline_keeps(self, title):
        assert not _pipeline_drops(title), title

    def test_listing_prefilter(self):
        spider = BaseJobSpider(name="t")
        assert spider.skip_from_listing("Account Executive, AI", "Austin, TX")
        assert spider.skip_from_listing("Recruiter, Engineering", "Austin, TX")
        assert not spider.skip_from_listing("Data Engineer, Sales", "Austin, TX")
        assert not spider.skip_from_listing("Sales Engineer", "Austin, TX")


@pytest.mark.parametrize("title,dropped", [
    ("Senior Specialist, CRM Business Analyst, Sales Enablement", False),
    ("Senior Business Analyst - Business Analysis for Talent Acquisition", False),
    ("Principal, Product Mgmt - Tech, Amazon Fulfillment Technologies (AFT)", False),
    ("Business Analyst Recruiting Manager- S&G", True),
    ("Business Analyst, Finance Systems", False),
])
def test_business_analyst_family_live_crawl(title, dropped):
    """Found on the first live crawl with the new filter (2026-11-07)."""
    assert is_non_it_title(title) is dropped
