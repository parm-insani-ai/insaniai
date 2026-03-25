/* ═══════════════════════════════════════════════
   DATA — Source configs and seed data.
   
   The project database is NO LONGER hardcoded here.
   All project data comes from the backend API.
   
   This file only contains:
   - SOURCES: connected data source display configs
   - SEED_DATA: used by the seed function to populate
     the backend with demo data on first run
   ═══════════════════════════════════════════════ */

// Source display configs (how they appear in the sidebar)
const SOURCES = {
  procore:  { name: "Procore",          color: "var(--orange)",     status: "Connected", lastSync: "2 min ago",  items: "RFIs, Submittals, Change Orders, Daily Logs", docs: "1,247 documents indexed" },
  autodesk: { name: "Autodesk BIM 360", color: "var(--blue)",       status: "Connected", lastSync: "8 min ago",  items: "3D Models, Clash Reports, Drawings, Issues",  docs: "834 documents indexed" },
  sage:     { name: "Sage 300",         color: "var(--green)",      status: "Connected", lastSync: "15 min ago", items: "Cost Reports, Invoices, Budget Lines, COs",    docs: "562 documents indexed" },
  email:    { name: "Email / Outlook",  color: "var(--text-muted)", status: "Connected", lastSync: "5 min ago",  items: "Project Threads, Attachments, Meeting Notes",  docs: "2,891 emails indexed" }
};

// Demo project data — used to seed the backend via apiCreateProject()
const SEED_PROJECTS = [
  {
    name: "Midtown Tower B",
    type: "Mixed-use high-rise, 32 stories",
    location: "245 W 54th St, NYC",
    data_json: {
      owner: "Meridian Development Group", gc: "Turner-Beck JV", architect: "SHoP Architects",
      pct: 66, contract: "$48.2M", gmp: "$50.3M", billed: "$31.7M", projected: "$51.5M",
      variance: "+2.4%", schedule: "3 days behind", critical: "Curtain wall → L8-14", subs: 23,
      rfis: [
        { id: "RFI-0347", title: "Embed plate revision grid C-7, L12", status: "Open", days: 11, to: "WKS Structural", src: "Procore", pri: "Critical", impact: "May delay L12 pour Mar 18." },
        { id: "RFI-0351", title: "Rebar vs MEP at shear wall W-3", status: "Open", days: 6, to: "MEP Consultants", src: "Autodesk BIM 360", pri: "High", impact: 'Structural rev 7 vs MEP rev 12.' },
        { id: "RFI-0344", title: "Waterproofing at P2 transition", status: "Open", days: 17, to: "Architect of Record", src: "Procore", pri: "Medium", impact: "Inspector flagged membrane termination." },
        { id: "RFI-0352", title: "Fire-rated shaft wall stair B", status: "Open", days: 4, to: "Code Consultants", src: "Procore", pri: "Medium", impact: "UL assembly needed. 2-wk float." }
      ],
      submittals: [
        { id: "S-221", title: "Curtain wall shop dwgs (resub)", status: "Pending", overdue: 12, sub: "ClearView Glazing", impact: "Blocks facade L8-14. CRITICAL PATH." },
        { id: "S-225", title: "Mech penthouse AHU specs", status: "Under Review", overdue: 0, sub: "AirFlow Mechanical" },
        { id: "S-230", title: "Fire-rated partitions L5-12", status: "Pending", overdue: 5, sub: "BuildRight Interiors" },
        { id: "S-233", title: "Roofing membrane", status: "Under Review", overdue: 0, sub: "Summit Roofing" }
      ],
      budget: {
        items: [
          { d: "Concrete", b: "$8.4M", p: "$8.5M" }, { d: "Steel", b: "$6.8M", p: "$6.9M" },
          { d: "Curtain Wall", b: "$5.2M", p: "$5.6M" },
          { d: "Electrical", b: "$4.1M", p: "$4.44M", alert: "8.2% over — CO-041($84K)+CO-043($127K) not in forecast" },
          { d: "HVAC", b: "$5.5M", p: "$5.6M" }
        ],
        cos: [{ id: "CO-041", d: "Tenant circuits L18-22", amt: "$84K" }, { id: "CO-043", d: "Generator upgrade", amt: "$127K" }]
      },
      schedule: { milestones: [{ n: "L12 Pour", dt: "Mar 18", s: "At Risk" }, { n: "Curtain Wall L8", dt: "Apr 1", s: "At Risk" }, { n: "Completion", dt: "Mar 2027", s: "Monitoring" }] },
      emails: [{ from: "Inspector Reeves", dt: "Mar 5", re: "P2 Waterproofing" }, { from: "PM Chen", dt: "Mar 7", re: "Electrical Budget Alert" }]
    }
  },
  {
    name: "Harbor View Complex",
    type: "Waterfront residential, 3 buildings",
    location: "Pier 17, Brooklyn, NY",
    data_json: { pct: 41, contract: "$32.5M", schedule: "On Track", rfis: [], submittals: [] }
  },
  {
    name: "Westfield Medical Center",
    type: "Medical office building, 5 stories",
    location: "120 Medical Dr, NJ",
    data_json: { pct: 82, contract: "$18.9M", schedule: "2 days ahead", rfis: [], submittals: [] }
  }
];

/**
 * Seed the backend with demo projects.
 * Called once after first signup if user has no projects.
 */
async function seedDemoProjects() {
  for (const p of SEED_PROJECTS) {
    try {
      await apiCreateProject(p.name, p.type, p.location, p.data_json);
    } catch (e) {
      console.warn('Seed failed for', p.name, e.message);
    }
  }
}
