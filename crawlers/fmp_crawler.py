import logging
from omegaconf import OmegaConf    # type: ignore
import json
from datetime import datetime
from typing import Dict, Any

from core.crawler import Crawler
from core.utils import create_session_with_retries

def is_date_in_range(datetime_str: str, start_year: int, end_year: int) -> bool:
    dt = datetime.strptime(datetime_str.split(' ')[0], '%Y-%m-%d')
    return start_year <= dt.year <= end_year

# Crawler for financial information using the financialmodelingprep.com service
# To use this crawler you have to have an fmp API_key in your secrets.toml profile
class FmpCrawler(Crawler):
    
    def __init__(self, cfg: OmegaConf, endpoint: str, customer_id: str, corpus_id: int, api_key: str) -> None:
        super().__init__(cfg, endpoint, customer_id, corpus_id, api_key)
        self.tickers = cfg.fmp_crawler.tickers
        self.start_year = int(cfg.fmp_crawler.start_year)
        self.end_year = int(cfg.fmp_crawler.end_year)
        self.api_key = cfg.fmp_crawler.fmp_api_key
        self.session = create_session_with_retries()

    def index_doc(self, document: Dict[str, Any]) -> bool:
        try:
            succeeded = self.indexer.index_document(document)
            if succeeded:
                logging.info(f"Indexed {document['documentId']}")
            else:
                logging.info(f"Error indexing issue {document['documentId']}")
            return succeeded
        except Exception as e:
            logging.info(f"Error during indexing of {document['documentId']}: {e}")
            return False

    def crawl(self) -> None:
        base_url = 'https://financialmodelingprep.com'
        for ticker in self.tickers:
            # get profile
            url = f'{base_url}/api/v3/profile/{ticker}?apikey={self.api_key}'
            try:
                response = self.session.get(url)
            except Exception as e:
                logging.info(f"Error getting transcript for {ticker} quarter {quarter} of {year}: {e}")   # type: ignore
                continue
            if response.status_code == 200:
                data = response.json()
                company_name = data[0]['companyName']
                logging.info(f"Processing {company_name}")
            else:
                logging.info(f"Can't get company profile for {ticker} - skipping")
                continue

            # index 10-K for ticker in date range
            url = f'{base_url}/api/v3/sec_filings/{ticker}?type=10-K&page=0&apikey={self.api_key}'
            filings = self.session.get(url).json()
            for year in range(self.start_year, self.end_year+1):
                url = f'{base_url}/api/v4/financial-reports-json?symbol={ticker}&year={year}&period=FY&apikey={self.api_key}'
                try:
                    response = self.session.get(url)
                except Exception as e:
                    logging.info(f"Error getting transcript for {ticker} quarter {quarter} of {year}: {e}")    # type: ignore
                    continue
                if response.status_code == 200:
                    data = response.json()
                    doc_title = f"10-K for {company_name} from {year}"
                    rel_filings = [f for f in filings if f['acceptedDate'][:4] == str(year)]
                    url = rel_filings[0]['finalLink'] if len(rel_filings)>0 else None
                    metadata = {'source': 'finsearch', 'title': doc_title, 'ticker': ticker, 'company name': company_name, 'year': year, 'type': '10-K', 'url': url}
                    document = {
                        "documentId": f"10-K-{company_name}-{year}",
                        "title": doc_title,
                        "metadataJson": json.dumps(metadata),
                        "section": []
                    }
                    for key in data.keys():
                        if type(data[key])==str:
                            continue
                        for item_dict in data[key]:
                            for title, values in item_dict.items():
                                values = [v for v in values if v and type(v)==str and len(v)>=10]
                                if len(values)>0 and len(' '.join(values))>100:
                                    document['section'].append({'title': f'{key} - {title}', 'text': '\n'.join(values)})   # type: ignore
                    self.index_doc(document)

            # Index earnings call transcript
            logging.info(f"Getting transcripts")
            for year in range(self.start_year, self.end_year+1):
                for quarter in range(1, 5):
                    url = f'{base_url}/api/v3/earning_call_transcript/{ticker}?quarter={quarter}&year={year}&apikey={self.api_key}'
                    try:
                        response = self.session.get(url)
                    except Exception as e:
                        logging.info(f"Error getting transcript for {company_name} quarter {quarter} of {year}: {e}")
                        continue
                    if response.status_code == 200:
                        for transcript in response.json():
                            title = f"Earnings call transcript for {company_name}, quarter {quarter} of {year}"
                            metadata = {'source': 'finsearch', 'title': title, 'ticker': ticker, 'company name': company_name, 'year': year, 'quarter': quarter, 'type': 'transcript'}
                            document = {
                                "documentId": f"transcript-{company_name}-{year}-{quarter}",
                                "title": title,
                                "metadataJson": json.dumps(metadata),
                                "section": [
                                    {
                                        'text': transcript['content']     # type: ignore
                                    }
                                ]
                            }
                            self.index_doc(document)
