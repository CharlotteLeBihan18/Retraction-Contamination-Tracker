// Sample graph for tests and demos (invented data, ~20 papers, dentistry theme).
// Everything created here has sample = true, so it can be removed with:
//   MATCH (n {sample: true}) DETACH DELETE n;
// Exception: Reason nodes are shared by name with the real data, so they are never flagged.
// After the delete, only the reasons that no notice uses any more are removed.
MATCH (n {sample: true}) DETACH DELETE n;
MATCH (r:Reason) WHERE NOT (r)<-[:FOR_REASON]-() DELETE r;

CREATE CONSTRAINT paper_doi IF NOT EXISTS FOR (p:Paper) REQUIRE p.doi IS UNIQUE;
CREATE CONSTRAINT author_id IF NOT EXISTS FOR (a:Author) REQUIRE a.openalex_id IS UNIQUE;
CREATE CONSTRAINT journal_name IF NOT EXISTS FOR (j:Journal) REQUIRE j.name IS UNIQUE;
CREATE CONSTRAINT reason_name IF NOT EXISTS FOR (r:Reason) REQUIRE r.name IS UNIQUE;
CREATE INDEX paper_year IF NOT EXISTS FOR (p:Paper) ON (p.year);

// Papers and their journals
UNWIND [
 {doi:'10.0000/sample.001', year:2016, journal:'Journal of Sample Dentistry', title:'Fluoride varnish halves caries in children: a randomized trial', abstract:'A randomized trial of 600 children reports that twice-yearly fluoride varnish halves new caries lesions.'},
 {doi:'10.0000/sample.002', year:2017, journal:'Oral Health Letters', title:'A peptide gel regenerates tooth enamel', abstract:'We describe a peptide gel that regenerates enamel in vitro within two weeks.'},
 {doi:'10.0000/sample.003', year:2018, journal:'International Dental Reports', title:'Herbal mouthwash outperforms chlorhexidine', abstract:'An herbal mouthwash reduced plaque more than chlorhexidine in a six-week study.'},
 {doi:'10.0000/sample.011', year:2017, journal:'Journal of Sample Dentistry', title:'Caries prevention in primary schools', abstract:'This school-based programme builds on the fluoride varnish trial and adopts its twice-yearly schedule.'},
 {doi:'10.0000/sample.012', year:2018, journal:'Oral Health Letters', title:'The cost of fluoride programmes', abstract:'We estimate programme costs and cite the fluoride varnish trial only as one of several published efficacy figures.'},
 {doi:'10.0000/sample.013', year:2020, journal:'International Dental Reports', title:'Review of pediatric dental guidelines', abstract:'Our pediatric guideline review relies on the fluoride varnish trial to recommend twice-yearly application.'},
 {doi:'10.0000/sample.014', year:2022, journal:'Journal of Sample Dentistry', title:'Varnish adoption in dental clinics', abstract:'Clinics adopted varnish at different rates, and an early trial is mentioned as background and was not used in our analysis.'},
 {doi:'10.0000/sample.015', year:2018, journal:'Oral Health Letters', title:'Peptide gel for dentin repair', abstract:'We extend the enamel peptide gel to dentin and rely on its reported regeneration rate.'},
 {doi:'10.0000/sample.016', year:2021, journal:'Clinical Periodontology Notes', title:'Enamel regeneration claims remain unproven', abstract:'A short commentary notes that enamel regeneration claims remain unproven and criticises the peptide gel study.'},
 {doi:'10.0000/sample.017', year:2019, journal:'International Dental Reports', title:'Comparing commercial mouthwashes', abstract:'We compare mouthwashes and use the herbal mouthwash results as our baseline for plaque reduction.'},
 {doi:'10.0000/sample.018', year:2022, journal:'Clinical Periodontology Notes', title:'A survey of oral rinses on the market', abstract:'Our survey of oral rinses lists the herbal mouthwash study among many products on the market.'},
 {doi:'10.0000/sample.021', year:2019, journal:'Journal of Sample Dentistry', title:'Meta-analysis of caries interventions', abstract:'A meta-analysis of caries interventions pooling school programmes and cost studies.'},
 {doi:'10.0000/sample.022', year:2021, journal:'Clinical Periodontology Notes', title:'Applying pediatric guidelines in 40 clinics', abstract:'A cohort study applying pediatric guideline recommendations in 40 clinics.'},
 {doi:'10.0000/sample.023', year:2023, journal:'Oral Health Letters', title:'Umbrella review of caries prevention', abstract:'An umbrella review of caries prevention meta-analyses.'},
 {doi:'10.0000/sample.024', year:2022, journal:'International Dental Reports', title:'Gel-based enamel treatments in a clinical trial', abstract:'A trial of gel-based enamel treatments inspired by earlier regeneration work.'},
 {doi:'10.0000/sample.025', year:2023, journal:'Journal of Sample Dentistry', title:'Emerging remineralisation treatments', abstract:'We review emerging remineralisation treatments, citing both the varnish trial and the peptide gel study.'},
 {doi:'10.0000/sample.031', year:2018, journal:'Oral Health Letters', title:'Ten-year survival of dental implants', abstract:'Ten-year survival of dental implants in 1200 patients.'},
 {doi:'10.0000/sample.032', year:2020, journal:'Clinical Periodontology Notes', title:'Peri-implant bone loss over time', abstract:'Peri-implant bone loss over time, building on a ten-year implant survival study.'},
 {doi:'10.0000/sample.033', year:2021, journal:'Journal of Sample Dentistry', title:'Orthodontic retention protocols', abstract:'A trial of orthodontic retention protocols.'},
 {doi:'10.0000/sample.034', year:2019, journal:'Oral Health Letters', title:'The oral microbiome and implant outcomes', abstract:'A review of the oral microbiome and implant outcomes.'}
] AS row
MERGE (p:Paper {doi: row.doi})
SET p.title = row.title, p.year = row.year, p.abstract = row.abstract, p.sample = true
MERGE (j:Journal {name: row.journal})
SET j.sample = true
MERGE (p)-[:PUBLISHED_IN]->(j);

// Authors
UNWIND [
 {id:'A01', doi:'10.0000/sample.001'}, {id:'A02', doi:'10.0000/sample.001'},
 {id:'A03', doi:'10.0000/sample.002'},
 {id:'A04', doi:'10.0000/sample.003'}, {id:'A01', doi:'10.0000/sample.003'},
 {id:'A05', doi:'10.0000/sample.011'}, {id:'A06', doi:'10.0000/sample.012'},
 {id:'A05', doi:'10.0000/sample.013'}, {id:'A07', doi:'10.0000/sample.013'},
 {id:'A08', doi:'10.0000/sample.014'}, {id:'A09', doi:'10.0000/sample.015'},
 {id:'A10', doi:'10.0000/sample.016'}, {id:'A04', doi:'10.0000/sample.017'},
 {id:'A11', doi:'10.0000/sample.018'}, {id:'A12', doi:'10.0000/sample.021'},
 {id:'A13', doi:'10.0000/sample.022'}, {id:'A12', doi:'10.0000/sample.023'},
 {id:'A14', doi:'10.0000/sample.024'}, {id:'A08', doi:'10.0000/sample.025'},
 {id:'A14', doi:'10.0000/sample.031'}, {id:'A15', doi:'10.0000/sample.032'},
 {id:'A05', doi:'10.0000/sample.033'}, {id:'A14', doi:'10.0000/sample.034'}
] AS row
MATCH (p:Paper {doi: row.doi})
MERGE (a:Author {openalex_id: row.id})
SET a.name = 'Sample Author ' + substring(row.id, 1), a.sample = true
MERGE (a)-[:AUTHORED]->(p);

// Retractions: label, notices and reasons
UNWIND [
 {doi:'10.0000/sample.001', date:'2019-03-01', source:'Retraction Watch', reasons:['Fabrication']},
 {doi:'10.0000/sample.002', date:'2020-06-15', source:'Retraction Watch', reasons:['Fabrication','Image manipulation']},
 {doi:'10.0000/sample.003', date:'2021-09-30', source:'Retraction Watch', reasons:['Plagiarism']},
 {doi:'10.0000/sample.003', date:'2021-10-12', source:'OpenAlex', reasons:[]}
] AS row
MATCH (p:Paper {doi: row.doi})
SET p:Retracted
CREATE (n:RetractionNotice {date: date(row.date), source: row.source, sample: true})
CREATE (n)-[:RETRACTS]->(p)
WITH n, row
UNWIND row.reasons AS reason
MERGE (r:Reason {name: reason})
MERGE (n)-[:FOR_REASON]->(r);

// Citations (before_retraction only for citations that point at retracted papers)
UNWIND [
 {citing:'10.0000/sample.011', cited:'10.0000/sample.001', before:true},
 {citing:'10.0000/sample.012', cited:'10.0000/sample.001', before:true},
 {citing:'10.0000/sample.013', cited:'10.0000/sample.001', before:false},
 {citing:'10.0000/sample.014', cited:'10.0000/sample.001', before:false},
 {citing:'10.0000/sample.025', cited:'10.0000/sample.001', before:false},
 {citing:'10.0000/sample.015', cited:'10.0000/sample.002', before:true},
 {citing:'10.0000/sample.016', cited:'10.0000/sample.002', before:false},
 {citing:'10.0000/sample.025', cited:'10.0000/sample.002', before:false},
 {citing:'10.0000/sample.017', cited:'10.0000/sample.003', before:true},
 {citing:'10.0000/sample.018', cited:'10.0000/sample.003', before:false},
 {citing:'10.0000/sample.021', cited:'10.0000/sample.011', before:null},
 {citing:'10.0000/sample.021', cited:'10.0000/sample.012', before:null},
 {citing:'10.0000/sample.022', cited:'10.0000/sample.013', before:null},
 {citing:'10.0000/sample.023', cited:'10.0000/sample.021', before:null},
 {citing:'10.0000/sample.024', cited:'10.0000/sample.016', before:null},
 {citing:'10.0000/sample.032', cited:'10.0000/sample.031', before:null},
 {citing:'10.0000/sample.034', cited:'10.0000/sample.031', before:null}
] AS row
MATCH (a:Paper {doi: row.citing}), (b:Paper {doi: row.cited})
MERGE (a)-[c:CITES]->(b)
SET c.before_retraction = row.before;