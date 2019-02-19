import datetime
import time
import logging
import json
from collections import OrderedDict
from commonUtil import CommonUtil

# Get config items
# Read Entity -> last_extracted_time
# Prepare URL for filter._id.min = last_extracted_time + 1
# Get Count of entities for above URL
# Now invoke extended_fetch API for specified pagesize in loop
# Tranform each response by applying entity specific XSLT
# print the response as tab delimited entity records..

logging = logging.getLogger("DataExtractor")

class TicketLogExtractor:
    
    CONFIG = None
    PAGE_SIZE = 10
    LAST_TIME = 0
    URI = ''
    STDOUT = 1
    TOTAL_URL_ARGS = None
    DATA_URL_ARGS = None
    ENTITY = None
    Util, FIELD_NAMES = None, None
    ETL_JOB_ID = None
    ETL_BEGIN_STAMP = 0
    WRITE_TO_KVSTORE = 0
    WRITE_TO_TSV = 0
    SPLUNK_KVSTORE_URI = None

    """Module level variables"""
    TOTAL_MATCHED_CNT = 0
    TOTAL_EXTRACTED_CNT = 0
    #MAX_TKT_LOG_ID = 0
    MAX_EDIT_DATE = 0
    END_STAMP = 0
    BEGIN_STAMP = 0
    ON_DEMAND_ETL = False
    
    """A Data extraction class for an entity"""
    def __init__(self, entity, config, begin_epoch, end_epoch, uri, totalMatchedURLArgs, pullDataURLArgs, etl_job_id):
        logging.debug("In __init__")
        self.CONFIG = config
        self.ENTITY = entity
        self.BEGIN_STAMP = begin_epoch
        self.END_STAMP = end_epoch
        self.ON_DEMAND_ETL = False if self.BEGIN_STAMP == None or self.END_STAMP == None else True
        self.ETL_JOB_ID = etl_job_id
        self.Util = CommonUtil(self.ENTITY, self.CONFIG, self.ETL_JOB_ID)
        self.URI = uri
        self.PAGE_SIZE = int(self.CONFIG.get(entity, "page_size"))
        self.LAST_TIME = int(self.CONFIG.get(entity, "last_extracted_time"))
        self.FIELD_NAMES = str(self.CONFIG.get(entity, "field_names")).split(self.Util.TAB)
        self.TOTAL_URL_ARGS = totalMatchedURLArgs
        self.DATA_URL_ARGS = pullDataURLArgs
        self.STDOUT = int(self.CONFIG.get(self.Util.CFG_DE_SEC, "write_extracted_data_to_console")) 
        self.WRITE_TO_KVSTORE = int(self.CONFIG.get(self.ENTITY, "write_to_kvstore"))
        self.WRITE_TO_TSV = int(self.CONFIG.get(self.ENTITY, "write_to_tsv")) 
        self.SPLUNK_KVSTORE_URI = str(self.CONFIG.get(self.ENTITY, "splunk_kvstore_uri")) 
        logging.debug("PAGE_SIZE:" + str(self.PAGE_SIZE) + ", LAST_TIME:" + str(self.LAST_TIME))
        logging.debug("Match Count URL Args: " + str(self.TOTAL_URL_ARGS))
        logging.debug("Data URL Args: " + str(self.DATA_URL_ARGS))
        logging.debug("Out __init__")
    
    def extractTicketLog(self):
        logging.debug("In extractTicketLog")
        status = False
        auditMsg = ''
        try:
            status = self._extractTicketLog()
            if self.TOTAL_MATCHED_CNT <= 0:
                self.Util.addAuditLogEntry(self.BEGIN_STAMP, self.END_STAMP, self.TOTAL_MATCHED_CNT, 0, self.ETL_BEGIN_STAMP, self.Util.AUDIT_MATCH_CNT_ZERO)
            elif status == True:
                if self.ON_DEMAND_ETL == False: self.Util.setConfigProperty(self.Util.CFG_LAST_EXT_TIME, self.END_STAMP)
                self.Util.addAuditLogEntry(self.BEGIN_STAMP, self.END_STAMP, self.TOTAL_MATCHED_CNT, self.TOTAL_EXTRACTED_CNT, self.ETL_BEGIN_STAMP, self.Util.AUDIT_SUCCESS)
        except Exception, args:
            status = False
            auditMsg = self.Util.AUDIT_ERROR(str(args))
            logging.error(auditMsg)
            if self.ON_DEMAND_ETL == False: self.Util.setConfigProperty(self.Util.CFG_LAST_EXT_TIME, self.MAX_EDIT_DATE)
            self.Util.addAuditLogEntry(self.BEGIN_STAMP, self.END_STAMP, self.TOTAL_MATCHED_CNT, self.TOTAL_EXTRACTED_CNT, self.ETL_BEGIN_STAMP, auditMsg)
        
        logging.debug("Out extractTicketLog")
        return status
        
    """This function 1st extracts new and then updated entity items"""
    def _extractTicketLog(self):
        logging.debug("In _extractTicketLog")
        self.ETL_BEGIN_STAMP = int(time.time())
        if self.END_STAMP == None: self.END_STAMP = int(time.time())
        
        if self.BEGIN_STAMP == None:
            self.BEGIN_STAMP = self.LAST_TIME - int(self.CONFIG.get(self.ENTITY, "prepone_extraction_window_secs"))
        if int(self.BEGIN_STAMP) <= 0: self.BEGIN_STAMP = 1
        self.MAX_EDIT_DATE = self.BEGIN_STAMP
        
        if 'order._id' in self.DATA_URL_ARGS: del self.DATA_URL_ARGS['order._id']
        
        # initArugments
        self.TOTAL_URL_ARGS['link_disp_field'] = '_id'
        # Workaround for DE8092 - EM7 ticket log data missing from Splunk | SAP
        self.TOTAL_URL_ARGS['order._id'] = 'ASC'
        update_key = 'filter.%s.max' % (self.CONFIG.get(self.ENTITY, "update_date_field"))
        self.TOTAL_URL_ARGS[update_key] = str(self.END_STAMP)
        self.DATA_URL_ARGS['filter.%s.min' % (self.CONFIG.get(self.ENTITY, "update_date_field"))] = str(self.BEGIN_STAMP)
        self.DATA_URL_ARGS[update_key] = str(self.END_STAMP)
        
        self.TOTAL_MATCHED_CNT, minID = self.Util.getTotalMatchedCount(self.URI, self.TOTAL_URL_ARGS, self.BEGIN_STAMP)
        self.TOTAL_MATCHED_CNT, minID = map(int, [self.TOTAL_MATCHED_CNT, minID])
        if self.TOTAL_MATCHED_CNT <= 0:
            logging.info(self.Util.AUDIT_MATCH_CNT_ZERO)
            return True

        logging.info("Number of total record matched is : %s", str(self.TOTAL_MATCHED_CNT))
        xsltContent = self.Util.getXsltContent()
        outputFileDateTime = str(datetime.datetime.now().strftime("%Y%m%d"))
        dataFolderLocation = self.CONFIG.get(self.Util.CFG_DE_SEC, "datalocation")
        self.Util.createEntityLocation(self.ENTITY, dataFolderLocation)

        ticketLogIDsChunkList = self.createTicketLogIDsChunks(minID)
        outputFile = '%s_%s.tsv' % (self.ENTITY, outputFileDateTime)
        self.extractTicketLogPage(ticketLogIDsChunkList, xsltContent, outputFile, dataFolderLocation)

        logging.info("Extracted count for entity %s is %s", self.ENTITY, str(self.TOTAL_EXTRACTED_CNT))
        logging.debug("Last edited Epoch time for %s is %s", self.ENTITY, str(self.END_STAMP))
        logging.debug("Out _extractTicketLog")
        return True
    
    def extractTicketLogPage(self, ticketLogIDsChunkList, xsltContent, outputFile, dataFolderLocation):
        logging.debug("In extractTicketLogPage")
        
        limit, offset = self.PAGE_SIZE, 0
        includeLastTime = True
        try:
            for ticketLogIDsChunk in ticketLogIDsChunkList:
                chunk_max_edit_date = 0
                logging.debug("Offset: %s, Limit: %s", str(offset), str(limit))
                self.DATA_URL_ARGS['filter._id.min'] = str(ticketLogIDsChunk[0])
                self.DATA_URL_ARGS['filter._id.max'] = str(ticketLogIDsChunk[1])
                pageLines = self.Util.extractEntityPage(self.URI, limit, offset, xsltContent, self.DATA_URL_ARGS, includeLastTime, self.BEGIN_STAMP)
                if pageLines == None or len(pageLines) == 0:
                    logging.debug("No data is returned between ticket log IDs: %s." %str(ticketLogIDsChunk))
                    continue
                
                entity_list, chunk_max_edit_date = self._processTicketLogPageLines(pageLines)
            
                if entity_list and len(entity_list) > 0:
                    self.TOTAL_EXTRACTED_CNT = self.TOTAL_EXTRACTED_CNT + len(entity_list)
                    if self.STDOUT >= 1:
                        for val in entity_list:
                            print val
                    
                    if self.WRITE_TO_TSV >= 1:
                        field_names = self.Util.TAB.join(self.FIELD_NAMES)
                        entityLines = self.Util.getPageRemainderLines(entity_list)
                        self.Util.persistPageData(self.ENTITY, outputFile, dataFolderLocation, entityLines, field_names)
                    if chunk_max_edit_date > self.MAX_EDIT_DATE: self.MAX_EDIT_DATE = chunk_max_edit_date
                else:
                    logging.debug("No data is returned between ticket log IDs: %s." %str(ticketLogIDsChunk))
        except Exception as ex:
            raise Exception("Error occurred while extracting data between ticket log IDs: %s. Error: %s" %(str(ticketLogIDsChunk), self.Util.AUDIT_ERROR(str(ex))))                
        logging.debug("Out extractTicketLogPage")

    def _processTicketLogPageLines(self, pageLines):
        logging.debug("In _processTicketLogPageLines")
        entity_list = list()
        max_edit_date = 0
        
        for pageLine in pageLines:
            fldList = list()
            try:
                entityValues = OrderedDict.fromkeys(self.FIELD_NAMES, value="")
                fieldIdx = 0
                fldList = pageLine.split(self.Util.TAB)
                entityValues["_key"] = fldList[0]
                for fldVal in fldList:
                    entityValues[self.FIELD_NAMES[fieldIdx]] = self.Util.formatValues(fldVal)
                    fieldIdx = fieldIdx + 1
                entity_list.append(entityValues)
                editDate = self._getTicketLogEditDate(entityValues)  
                if editDate > max_edit_date: max_edit_date = editDate 
            except Exception as ex:
                logging.warn("Unable to process %s entity record '%s'. Warning: %s", self.ENTITY, str(fldList), self.Util.filterSpecialChars(str(ex)))
        
        logging.debug("Out _processTicketLogPageLines")
        return entity_list, max_edit_date    
    
    def _getTicketLogEditDate(self, entityValues):
        try:
            return int(entityValues['ticket_log_edit_date'])
        except Exception:
            return 0
                   
    def createTicketLogIDsChunks(self, minID):
        logging.debug("In createTicketLogIDChunks")
        ticketLogIDsList = list()
        maxID = minID + self.TOTAL_MATCHED_CNT
        logging.info("Creating chunks between ticket log min ID:%s and max ID: %s", str(minID), str(maxID))
        while(maxID - minID > self.PAGE_SIZE):
            midID = minID + self.PAGE_SIZE - 1
            ticketLogIDsList.append((minID, midID))
            minID = midID + 1
        ticketLogIDsList.append((minID, maxID))
        logging.debug("In createTicketLogIDChunks")
        return ticketLogIDsList
    