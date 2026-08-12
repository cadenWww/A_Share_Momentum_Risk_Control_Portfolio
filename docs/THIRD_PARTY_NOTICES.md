# Third-Party and Data Notices

This project uses the following open-source Python packages:

- AkShare for access to public financial-data endpoints;
- NumPy for numerical operations;
- pandas for tabular and time-series analysis;
- Matplotlib for charts; and
- Python standard-library Tkinter components for the optional desktop interface.

The direct versions used for the frozen run are recorded in `requirements-lock.txt`. Every dependency remains subject to its own license and terms.

Market observations are retrieved from public endpoints exposed through AkShare. Availability of an endpoint does not grant ownership of the underlying market data. To limit redistribution, this repository excludes downloaded raw price caches and full adjusted-price matrices. It includes only derived outputs needed to review the method and findings.

The MIT License applies only to original repository code and documentation. It does not relicense dependencies, trademarks, or third-party market data. The software and historical analysis are supplied for academic and educational evaluation, without warranty, and are not investment advice.

