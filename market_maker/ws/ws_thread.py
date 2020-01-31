import sys
import websocket
import threading
import traceback
import ssl
from time import sleep
import json
import decimal
import logging
from market_maker.settings import settings
from market_maker.auth.APIKeyAuth import generate_expires, generate_signature
from market_maker.auth.APIKeyAuthWithExpires import *
from market_maker.utils.log import setup_custom_logger
from market_maker.utils.math import toNearest
from future.utils import iteritems
from future.standard_library import hooks
with hooks():  # Python 2/3 compat
    from urllib.parse import urlparse, urlunparse


# Connects to GTE websocket for streaming realtime data.
# The Marketmaker still interacts with this as if it were a REST Endpoint, but now it can get
# much more realtime data without heavily polling the API.
#
# The Websocket offers a bunch of data as raw properties right on the object.
# On connect, it synchronously asks for a push of all this data then returns.
# Right after, the MM can start using its data. It will be updated in realtime, so the MM can
# poll as often as it wants.
class GTEWebsocket():

    # Don't grow a table larger than this amount. Helps cap memory usage.
    MAX_TABLE_LEN = 200

    def __init__(self):
        self.logger = logging.getLogger('root')
        self.__reset()
        self.data = {}  #客户端维护的数据结构，完全不是消息体的 raw 数据
        self.ws_url = settings.WS_URL
        self.keys = {}


    def __del__(self):
        self.exit()

    # We run one symbol at a process. 
    # So no need to cover multiple instrument type/assets/symbols
    def connect(self, endpoint="",  shouldAuth=True):
        '''Connect to the websocket and initialize data stores.'''

        self.logger.debug("Connecting GTE WebSocket.")

        self.shouldAuth = shouldAuth

        wsURL = self.ws_url

        self.logger.info("Connecting to %s" % wsURL)
        self.__connect(wsURL)  # auth信息放在http header里面了
        self.logger.info('Connected to WS. Now to subscribe some sample data')

        # 我们约定单个进程不能跨货币区、跨品种；否则计算起来太麻烦
        # 如果想多交易区、多品种交易，就运行多个进程。
        sub_settle_currencys = 'BTC'
        instrument_type = 'pc'
        sub_symbols = ['BTC_USD']  #打算订阅/交易的品种


        for symbol in sub_symbols:
            args = {
                "instrument_type":instrument_type,
                "table":"instrument",
                "settle_currency":sub_settle_currencys,
                "symbol":symbol
            }
            self.__send_command('sub',args)
            args = {
                "instrument_type":instrument_type,
                "table":"trade",
                "settle_currency":sub_settle_currencys,
                "symbol":symbol
            } 
            self.__send_command('sub',args)
            args = {
                "instrument_type":instrument_type,
                "table":"order_book",
                "settle_currency":sub_settle_currencys,
                "symbol":symbol
            } 
            self.__send_command('sub',args)

        # Connected. Wait for partials
        # 确保收到第一条partial消息之后才完成初始化
        self.__wait_for_symbol(symbol)

        self.shouldAuth = True
        if self.shouldAuth:
            # ws 命令方式auth
            expires = int(round(time.time()) + 60)*1000  # 60s grace period in case of clock skew
            message = "GET/ws" + str(expires) 
            signature = hmac.new(bytes(settings.API_SECRET, 'utf8'), bytes(message, 'utf8'), digestmod=hashlib.sha256).hexdigest()
            args = { 
                "api_key" : settings.API_KEY, 
                "expires" : str(expires), 
                "signature" : signature 
                }
            self.logger.info('expires：'+ str(expires))
            self.logger.info('expmessage：'+ message)
            self.logger.info('signature：'+ signature)
            self.__send_command('auth_key_expires',args)
            
            # 订阅账户信息
            args = {
                "instrument_type":settings.INSTRUMENTTYPE,
                "table":"order",
                "settle_currency":settings.SETTLECURRENCY,
                "symbol":settings.SYMBOL
            }
            self.__send_command('sub',args)
            args = {
                "instrument_type":settings.INSTRUMENTTYPE,
                "table":"execution",
                "settle_currency":settings.SETTLECURRENCY,
                "symbol":settings.SYMBOL
            } 
            self.__send_command('sub',args)
            args = {
                "instrument_type":settings.INSTRUMENTTYPE,
                "table":"position",
                "settle_currency":settings.SETTLECURRENCY,
                "symbol":settings.SYMBOL
            } 
            self.__send_command('sub',args)

            self.__wait_for_account()
        self.logger.info('Got sample market data. Starting.')

    #
    # Data methods
    #
    def get_instrument(self, symbol):
        #self.logger.info(list(self.data.keys()))
        instruments = self.data['instrument']
        #self.logger.info(instruments)
        matchingInstruments = [i for i in instruments if i['symbol'] == symbol]
        if len(matchingInstruments) == 0:
            raise Exception("Unable to find instrument or index with symbol: " + symbol)
        instrument = matchingInstruments[0]
        # Turn the 'tickSize' into 'tickLog' for use in rounding
        # http://stackoverflow.com/a/6190291/832202
        instrument['tickLog'] = decimal.Decimal(str(instrument['tick_size'])).as_tuple().exponent * -1
        return instrument

    def get_ticker(self, symbol):
        '''Return a ticker object. Generated from instrument.'''

        instrument = self.get_instrument(symbol)

        # If this is an index, we have to get the data from the last trade.
        if instrument['symbol'][0] == '.':
            ticker = {}
            ticker['mid'] = ticker['buy'] = ticker['sell'] = ticker['last'] = instrument['markPrice']
        # Normal instrument
        else:
            # 我们先fake一点数据
            bid = float(instrument['last_price']) - 5
            ask = float(instrument['last_price']) + 2.5
            '''
            bid = instrument['bidPrice'] or instrument['last_price']
            ask = instrument['askPrice'] or instrument['last_price']
            '''
            ticker = {
                "last": instrument['last_price'],
                "buy": bid,
                "sell": ask,
                "mid": (bid + ask) / 2
            }

        # The instrument has a tickSize. Use it to round values.
        #return {k: toNearest(float(v or 0), instrument['tickSize']) for k, v in iteritems(ticker)}
        return {k: toNearest(float(v or 0), 0.5) for k, v in iteritems(ticker)}
        

    def funds(self):
        return self.data['margin'][0]

    def market_depth(self, symbol):
        raise NotImplementedError('orderBook is not subscribed; use askPrice and bidPrice on instrument')
        # return self.data['orderBook25'][0]

    def open_orders(self, clOrdIDPrefix):
        orders = self.data['order']
        # Filter to only open orders (leavesQty > 0) and those that we actually placed
        return [o for o in orders if str(o['clOrdID']).startswith(clOrdIDPrefix) and o['leavesQty'] > 0]

    # 返回指定结算区、指定工具类型、指定symbol的全部仓位
    # 返回结果是数组
    def position(self,instrument_type, settle_currency,symbol):
        positions = self.data['position']
        pos = [p for p in positions if p['instrument_type'] == instrument_type and p['settle_currency'] == settle_currency and p['symbol'] == symbol  ]
        if len(pos) == 0:
            # No position found; stub it
            #return {'avgCostPrice': 0, 'avgEntryPrice': 0, 'currentQty': 0, 'symbol': symbol}
            pass
        return pos

    def recent_trades(self):
        return self.data['trade']

    #
    # Lifecycle methods
    #
    def error(self, err):
        self._error = err
        self.logger.error(err)
        self.exit()

    def exit(self):
        self.exited = True
        self.ws.close()

    #
    # Private methods
    #

    def __connect(self, wsURL):
        '''Connect to the websocket in a thread.'''
        self.logger.debug("Starting thread")

        ssl_defaults = ssl.get_default_verify_paths()
        sslopt_ca_certs = {'ca_certs': ssl_defaults.cafile}
        self.ws = websocket.WebSocketApp(wsURL,
                                         on_message=self.__on_message,
                                         on_close=self.__on_close,
                                         on_open=self.__on_open,
                                         on_error=self.__on_error,
                                         header=self.__get_auth()
                                         )

        setup_custom_logger('websocket', log_level=settings.LOG_LEVEL)
        #self.wst = threading.Thread(target=lambda: self.ws.run_forever(sslopt=sslopt_ca_certs)) #需要ssl验证
        self.wst = threading.Thread(target=lambda: self.ws.run_forever(sslopt={"cert_reqs": ssl.CERT_NONE}))  #不要ssl验证
        self.wst.daemon = True
        self.wst.start()
        self.logger.info("Started thread")

        # Wait for connect before continuing
        conn_timeout = 5
        while (not self.ws.sock or not self.ws.sock.connected) and conn_timeout and not self._error:
            sleep(1)
            conn_timeout -= 1

        if not conn_timeout or self._error:
            self.logger.error("Couldn't connect to WS! Exiting.")
            self.exit()
            sys.exit(1)

    def __get_auth(self):
        '''Return auth headers. Will use API Keys if present in settings.'''

        if self.shouldAuth is False:
            return []

        self.logger.info("Authenticating with API Key.")
        # To auth to the WS using an API key, we generate a signature of a nonce and
        # the WS API endpoint.
        nonce = generate_expires()
        return [
            "api-expires: " + str(nonce),
            "api-signature: " + generate_signature(settings.API_SECRET, 'GET', '/realtime', nonce, ''),
            "api-key:" + settings.API_KEY
        ]

    def __wait_for_account(self):
        '''On subscribe, this data will come down. Wait for it.'''
        # Wait for the keys to show up from the ws
        # while not {'margin', 'position', 'order'} <= set(self.data):
        while not { 'position', 'order'} <= set(self.data):   # 暂时没有'margin',
            sleep(0.1)

    def __wait_for_symbol(self, symbol):
        '''On subscribe, this data will come down. Wait for it.'''
        while not {'instrument', 'order_book'} <= set(self.data):
            sleep(0.1)
            
    # 需要 command   args 两个参数，后两个可以为空
    def __send_command(self, command, args=None):
        '''Send a raw command.'''
        self.logger.debug(json.dumps({"op": command, "args": args or ""}))
        self.ws.send(json.dumps({"op": command, "args": args or ""}))

    def __on_message(self, message):
        '''Handler for parsing WS messages.'''
        message = json.loads(message)
        #self.logger.info(json.dumps(message))

        if 'status' in message:    # 是状态类消息
            if message['status'] == 400:
                self.error(message['error'])
            elif message['status'] == 401:
                self.error("API Key incorrect, please check and restart.")
            else:
                pass

            return
        elif 'data' in message:     # 是数据消息
            
            table = message['table'] if 'table' in message else None  # 主题
            action = message['action'] if 'action' in message else None
            if not action:
                self.logger.info(json.dumps(message))

            if table not in self.data:   # 例如 orderbookL2还没有
                self.data[table] = []

            if table not in self.keys:
                self.keys[table] = []

            # There are four possible actions from the WS:
            # 'partial' - full table image
            # 'insert'  - new row
            # 'update'  - update row
            # 'delete'  - delete row
            if action == 'partial':
                self.logger.debug("%s: partial" % table)
                self.data[table] += message['data']

                # Keys are communicated on partials to let you know how to uniquely identify
                # an item. We use it for updates.
                #self.keys[table] = message['data']['keys']  # self.keys[table] 存贮每个主题唯一识别字段
                # 现在我们手动赋值
                self.keys['instument'] = ['settle_currency','asset_class','symbol']
                self.keys['trade'] = ['settle_currency','asset_class','symbol']
                self.keys['order_book'] = ['id']

            elif action == 'insert':
                self.logger.debug('%s: inserting %s' % (table, message['data']))
                self.data[table] += message['data']

                # Limit the max length of the table to avoid excessive memory usage.
                # Don't trim orders because we'll lose valuable state if we do.
                if table not in ['order_book'] and len(self.data[table]) > GTEWebsocket.MAX_TABLE_LEN:
                    self.data[table] = self.data[table][(GTEWebsocket.MAX_TABLE_LEN // 2):]

            elif action == 'update':
                self.logger.debug('%s: updating %s' % (table, message['data']))
                # Locate the item in the collection and update it.
                for updateData in message['data']:
                    item = findItemByKeys(self.keys[table], self.data[table], updateData)
                    if not item:
                        self.logger.debug('updating data %s not found in %s' % ( updateData,table))
                        continue  # No item found to update. Could happen before push

                    # Log executions
                    if table == 'order':
                        is_canceled = 'ordStatus' in updateData and updateData['ordStatus'] == 'Canceled'
                        if 'cumQty' in updateData and not is_canceled:
                            contExecuted = updateData['cumQty'] - item['cumQty']
                            if contExecuted > 0:
                                instrument = self.get_instrument(item['symbol'])
                                self.logger.info("Execution: %s %d Contracts of %s at %.*f" %
                                            (item['side'], contExecuted, item['symbol'],
                                            instrument['tickLog'], item['price']))

                    # Update this item.
                    item.update(updateData)

                    # Remove canceled / filled orders
                    if table == 'order' and item['leavesQty'] <= 0:
                        self.data[table].remove(item)

            elif action == 'delete':
                self.logger.debug('%s: deleting %s' % (table, message['data']))
                # Locate the item in the collection and remove it.
                for deleteData in message['data']:
                    item = findItemByKeys(self.keys[table], self.data[table], deleteData)
                    self.data[table].remove(item)
            else:
                raise Exception("Unknown action: %s" % action)


    def __on_open(self):
        self.logger.info("Websocket Opened.")
        

    def __on_close(self):
        self.logger.info('Websocket Closed')
        self.exit()

    def __on_error(self, ws, error):
        if not self.exited:
            self.error(error)

    def __reset(self):
        self.data = {}
        self.keys = {}
        self.exited = False
        self._error = None

# keys:['symbol','id']
# table: self.data['orderbook']  整个交易所所有的品种；
# matchData: message['data']['rows']的一个item
def findItemByKeys(keys, table, matchData):
    for item in table:
        matched = True
        for key in keys:
            if item[key] != matchData[key]:
                matched = False
        if matched:
            return item

if __name__ == "__main__":
    # create console handler and set level to debug
    logger = logging.getLogger()
    logger.setLevel(logging.DEBUG)
    ch = logging.StreamHandler()
    # create formatter
    formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    # add formatter to ch
    ch.setFormatter(formatter)
    logger.addHandler(ch)
    ws = GTEWebsocket()
    ws.logger = logger
    ws.connect("wss://td.gte.io")
    while(ws.ws.sock.connected):
        sleep(1)

