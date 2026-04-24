import requests
import xml.etree.ElementTree as ET
from typing import List, Dict, Optional

PUBMED_BASE_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
# SSL_VERIFY=False works around corporate proxy / Windows cert-store issues in local dev.
# PubMed calls are read-only; set to True (or a CA bundle path) in production.
SSL_VERIFY = False


class PubMedClient:
    def __init__(self, api_key: Optional[str] = None, email: Optional[str] = None):
        self.api_key = api_key
        self.email = email
        self.base_params: Dict = {}
        if api_key:
            self.base_params["api_key"] = api_key
        if email:
            self.base_params["email"] = email

    def search(self, query: str, max_results: int = 50) -> List[str]:
        """Search PubMed and return list of PMIDs sorted by relevance."""
        params = {
            **self.base_params,
            "db": "pubmed",
            "term": query,
            "retmax": max_results,
            "retmode": "json",
            "sort": "relevance",
        }
        try:
            response = requests.get(
                f"{PUBMED_BASE_URL}/esearch.fcgi", params=params, timeout=30, verify=SSL_VERIFY
            )
            response.raise_for_status()
            data = response.json()
            return data.get("esearchresult", {}).get("idlist", [])
        except Exception as e:
            print(f"PubMed search error: {e}")
            return []

    def fetch_details(self, pmids: List[str]) -> List[Dict]:
        """Fetch full article details (title, abstract, authors, etc.) for PMIDs."""
        if not pmids:
            return []
        params = {
            **self.base_params,
            "db": "pubmed",
            "id": ",".join(pmids),
            "retmode": "xml",
            "rettype": "abstract",
        }
        try:
            response = requests.get(
                f"{PUBMED_BASE_URL}/efetch.fcgi", params=params, timeout=60, verify=SSL_VERIFY
            )
            response.raise_for_status()
            return self._parse_articles(response.text)
        except Exception as e:
            print(f"PubMed fetch error: {e}")
            return []

    def fetch_links(self, pmids: List[str]) -> Dict[str, List[str]]:
        """
        Fetch related article links via PubMed elink API.
        Used to build citation/relatedness graph for PageRank scoring.
        """
        if not pmids:
            return {}
        pmids = pmids[:20]  # Limit to avoid timeouts
        links: Dict[str, List[str]] = {}
        try:
            params = {
                **self.base_params,
                "dbfrom": "pubmed",
                "db": "pubmed",
                "id": ",".join(pmids),
                "linkname": "pubmed_pubmed",
                "retmode": "json",
                "cmd": "neighbor_score",
            }
            response = requests.get(
                f"{PUBMED_BASE_URL}/elink.fcgi", params=params, timeout=30, verify=SSL_VERIFY
            )
            response.raise_for_status()
            data = response.json()

            for linkset in data.get("linksets", []):
                ids = linkset.get("ids", [])
                if not ids:
                    continue
                source_id = str(ids[0])
                linked = []
                for linksetdb in linkset.get("linksetdbs", []):
                    if linksetdb.get("linkname") == "pubmed_pubmed":
                        linked = [str(i) for i in linksetdb.get("links", [])]
                links[source_id] = linked
        except Exception as e:
            print(f"PubMed links error: {e}")
        return links

    def _parse_articles(self, xml_text: str) -> List[Dict]:
        articles = []
        try:
            root = ET.fromstring(xml_text)
            for article in root.findall(".//PubmedArticle"):
                try:
                    pmid_el = article.find(".//PMID")
                    pmid = pmid_el.text if pmid_el is not None else ""

                    title_el = article.find(".//ArticleTitle")
                    title = "".join(title_el.itertext()) if title_el is not None else ""

                    abstract_els = article.findall(".//AbstractText")
                    abstract = " ".join(
                        "".join(el.itertext()) for el in abstract_els
                    )

                    year_el = article.find(".//PubDate/Year")
                    year = year_el.text if year_el is not None else ""

                    journal_el = article.find(".//Journal/Title")
                    journal = journal_el.text if journal_el is not None else ""

                    authors = []
                    for author in article.findall(".//Author"):
                        last = author.find("LastName")
                        first = author.find("ForeName")
                        if last is not None and last.text:
                            name = last.text
                            if first is not None and first.text:
                                name += f", {first.text}"
                            authors.append(name)

                    if pmid and (title or abstract):
                        articles.append({
                            "pmid": pmid,
                            "title": title,
                            "abstract": abstract,
                            "year": year,
                            "journal": journal,
                            "authors": authors[:5],
                            "url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
                        })
                except Exception:
                    continue
        except ET.ParseError as e:
            print(f"XML parse error: {e}")
        return articles
