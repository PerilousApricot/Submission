import os
import os.path
cpFiles=["Analyzer","Pileup"]
PathtoExecutable=os.getenv('ANALYSISDIR', os.path.join(os.getenv('CMSSW_BASE'), 'Analyzer' ))
outDir=os.path.join(os.getcwd(),"out")
