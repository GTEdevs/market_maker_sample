#coding=utf-8
from __future__ import absolute_import
from time import sleep
import sys
from datetime import datetime
from os.path import getmtime
import random
import requests
import atexit
import signal
import uuid

from market_maker import gte
from market_maker.settings import settings
from market_maker.utils import log, constants, errors, math

# Used for reloading the bot - saves modified times of key files
import os
watched_files_mtimes = [(f, getmtime(f)) for f in settings.WATCHED_FILES]


#
# Helpers
#
logger = log.setup_custom_logger('root')

class ExchangeInterface:
    def __init__(self, dry_run=False):
        self.dry_run = dry_run

        self.instrument_type = settings.INSTRUMENTTYPE
        self.settle_currency = settings.SETTLECURRENCY
        #if len(sys.argv) > 1:
        #    self.symbol = sys.argv[1]
        #else:
        #    self.symbol = settings.SYMBOL
        self.symbol = settings.SYMBOL
        self.gte = gte.GTE(base_url=settings.API_URL_BASE, 
                                    apiKey=settings.API_KEY, apiSecret=settings.API_SECRET,
                                    orderIDPrefix=settings.ORDERID_PREFIX, postOnly=settings.POST_ONLY,
                                    timeout=settings.TIMEOUT)

    def cancel_order(self, order):
        tickLog = self.get_instrument()['tickLog']
        logger.info("Canceling: %s %d @ %.*f" % (order['side'], order['orderQty'], tickLog, order['price']))
        while True:
            try:
                self.gte.cancel(order['orderID'])
                sleep(settings.API_REST_INTERVAL)
            except ValueError as e:
                logger.info(e)
                sleep(settings.API_ERROR_INTERVAL)
            else:
                break

    def cancel_all_orders(self):
        if self.dry_run:
            return

        logger.info("Resetting current position. Canceling all existing orders.")
        tickLog = self.get_instrument()['tickLog']

        # In certain cases, a WS update might not make it through before we call this.
        # For that reason, we grab via HTTP to ensure we grab them all.
        orders = self.gte.http_open_orders()

        for order in orders:
            logger.info("Canceling: %s %d @ %.*f" % (order['side'], order['orderQty'], tickLog, order['price']))

        if len(orders):
            self.gte.cancel([order['orderID'] for order in orders])

        sleep(settings.API_REST_INTERVAL)

    def get_portfolio(self):
        contracts = settings.CONTRACTS
        portfolio = {}
        for symbol in contracts:
            position = self.gte.position(symbol=symbol)
            instrument = self.gte.instrument(symbol=symbol)

            if instrument['isQuanto']:
                future_type = "Quanto"
            elif instrument['isInverse']:
                future_type = "Inverse"
            elif not instrument['isQuanto'] and not instrument['isInverse']:
                future_type = "Linear"
            else:
                raise NotImplementedError("Unknown future type; not quanto or inverse: %s" % instrument['symbol'])

            if instrument['underlyingToSettleMultiplier'] is None:
                multiplier = float(instrument['multiplier']) / float(instrument['quoteToSettleMultiplier'])
            else:
                multiplier = float(instrument['multiplier']) / float(instrument['underlyingToSettleMultiplier'])

            portfolio[symbol] = {
                "currentQty": float(position['currentQty']),
                "futureType": future_type,
                "multiplier": multiplier,
                "markPrice": float(instrument['markPrice']),
                "spot": float(instrument['indicativeSettlePrice'])
            }

        return portfolio

    def calc_delta(self):
        """Calculate currency delta for portfolio"""
        portfolio = self.get_portfolio()
        spot_delta = 0
        mark_delta = 0
        for symbol in portfolio:
            item = portfolio[symbol]
            if item['futureType'] == "Quanto":
                spot_delta += item['currentQty'] * item['multiplier'] * item['spot']
                mark_delta += item['currentQty'] * item['multiplier'] * item['markPrice']
            elif item['futureType'] == "Inverse":
                spot_delta += (item['multiplier'] / item['spot']) * item['currentQty']
                mark_delta += (item['multiplier'] / item['markPrice']) * item['currentQty']
            elif item['futureType'] == "Linear":
                spot_delta += item['multiplier'] * item['currentQty']
                mark_delta += item['multiplier'] * item['currentQty']
        basis_delta = mark_delta - spot_delta
        delta = {
            "spot": spot_delta,
            "mark_price": mark_delta,
            "basis": basis_delta
        }
        return delta

    # long仓位数量
    def get_long_delta(self, symbol=None):
        if symbol is None:
            symbol = self.symbol
        ps = self.get_position(symbol)
        #print(ps)
        for p in ps:
            if p['side'] == '1':
                return int(p['qty'])
        # 没找到
        return 0
    
    # short仓位数量
    def get_short_delta(self, symbol=None):
        if symbol is None:
            symbol = self.symbol
        ps = self.get_position(symbol)
        for p in ps:
            if p['side'] == '0':
                return int(p['qty'])
        # 没找到
        return 0

    def get_instrument(self, symbol=None):
        if symbol is None:
            symbol = self.symbol
        return self.gte.instrument(symbol)

    def get_margin(self):
        if self.dry_run:
            return {'marginBalance': float(settings.DRY_BTC), 'availableFunds': float(settings.DRY_BTC)}
        return self.gte.funds()

    def get_orders_ws(self):
        if self.dry_run:
            return []
        return self.gte.open_orders_ws()

    def get_orders_http(self):
        if self.dry_run:
            return []
        return self.gte.open_orders_http()

    def get_highest_buy(self):
        buys = [o for o in self.get_orders_http() if o['side'] == '1']
        if not len(buys):
            return {'price': -2**32}
        highest_buy = max(buys or [], key=lambda o: float(o['price']))
        return highest_buy if highest_buy else {'price': -2**32}

    def get_lowest_sell(self):
        sells = [o for o in self.get_orders_http() if o['side'] == '0']
        if not len(sells):
            return {'price': 2**32}
        lowest_sell = min(sells or [], key=lambda o: float(o['price']))
        return lowest_sell if lowest_sell else {'price': 2**32}  # ought to be enough for anyone

    def get_position(self, symbol=None):
        if symbol is None:
            symbol = self.symbol
        return self.gte.position_ws(self.instrument_type, self.settle_currency,symbol)

    def get_ticker(self, symbol=None):
        if symbol is None:
            symbol = self.symbol
        return self.gte.ticker_data(symbol)

    def is_open(self):
        """Check that websockets are still open."""
        return not self.gte.ws.exited

    
    #def check_market_open(self):
    #    instrument = self.get_instrument()
    #    if instrument["state"] != "Open" and instrument["state"] != "Closed":
    #        raise errors.MarketClosedError("The instrument %s is not open. State: %s" %
    #                                       (self.symbol, instrument["state"]))

    def check_if_orderbook_empty(self):
        """This function checks whether the order book is empty"""
        instrument = self.get_instrument()
        #if instrument['midPrice'] is None:  # 原代码
        if instrument['last_price'] is None:
            raise errors.MarketEmptyError("Orderbook is empty, cannot quote")
    
    '''
    def amend_bulk_orders(self, orders):
        if self.dry_run:
            return orders
        return self.gte.amend_bulk_orders(orders)

    def create_bulk_orders(self, orders):
        if self.dry_run:
            return orders
        return self.gte.create_bulk_orders(orders)

    def cancel_bulk_orders(self, orders):
        if self.dry_run:
            return orders
        return self.gte.cancel([order['orderID'] for order in orders])
    '''

class OrderManager:
    def __init__(self):
        self.instrument_type = settings.INSTRUMENTTYPE
        self.settle_currency = settings.SETTLECURRENCY
        self.symbol = settings.SYMBOL
        #if len(sys.argv) > 1:
        #    self.symbol = sys.argv[1]
        #else:
        #    self.symbol = settings.SYMBOL

        self.exchange = ExchangeInterface(settings.DRY_RUN)
        # Once exchange is created, register exit handler that will always cancel orders
        # on any error.
        atexit.register(self.exit)
        signal.signal(signal.SIGTERM, self.exit)

        #logger.info('settle:'+self.settle_currency)
        #print(settings)
        logger.info("Using settle_currency|symbol %s|%s." % (self.settle_currency,self.symbol))

        if settings.DRY_RUN:
            logger.info("Initializing dry run. Orders printed below represent what would be posted to GTE.")
        else:
            logger.info("Order Manager initializing, connecting to GTE. Live run: executing real trades.")

        self.start_time = datetime.now()
        self.instrument = self.exchange.get_instrument()
        self.starting_qty_long = self.exchange.get_long_delta()
        self.starting_qty_short = self.exchange.get_short_delta()

        self.running_qty_long = self.starting_qty_long
        self.running_qty_short = self.starting_qty_short



        self.reset()

    def reset(self):
        # 无需取消所有订单吧？
        #self.exchange.cancel_all_orders() 
        self.sanity_check()
        self.print_status()

        # Create orders and converge.
        self.place_orders()

    def print_status(self):
        """Print the current MM status."""

        #margin = self.exchange.get_margin()
        position = self.exchange.get_position() #这里返回的是数组
        self.running_qty_long = self.exchange.get_long_delta()
        self.running_qty_short = self.exchange.get_short_delta()

        tickLog = self.exchange.get_instrument()['tickLog']
        #self.start_XBt = margin["marginBalance"]
        self.start_XBt = 500

        logger.info("Current XBT Balance: %.6f" % XBt_to_XBT(self.start_XBt))
        logger.info("Current Contract Position: long %d ;short %d" % (self.running_qty_long,self.running_qty_short))
        if settings.CHECK_POSITION_LIMITS:
            logger.info("Position limits: %d/%d" % (settings.MIN_POSITION, settings.MAX_POSITION))
        #if int(position['qty']) != 0:
            #logger.info("Avg Cost Price: %.*f" % (tickLog, float(position['avgCostPrice'])))
            #logger.info("Avg Entry Price: %.*f" % (tickLog, float(position['avgEntryPrice'])))
        logger.info("Contracts Traded This Run: long %d; short %d" % (self.running_qty_long - self.starting_qty_long,self.running_qty_short - self.starting_qty_short ))
        #logger.info("Total Contract Delta: %.4f XBT" % self.exchange.calc_delta()['spot'])

    def get_ticker(self):
        ticker = self.exchange.get_ticker()
        tickLog = self.exchange.get_instrument()['tickLog']

        # Set up our buy & sell positions as the smallest possible unit above and below the current spread
        # and we'll work out from there. That way we always have the best price but we don't kill wide
        # and potentially profitable spreads.
        #self.start_position_buy = ticker["buy"] + self.instrument['tickSize']
        #self.start_position_sell = ticker["sell"] - self.instrument['tickSize']
        self.start_position_buy = ticker["buy"] + 0.5
        self.start_position_sell = ticker["sell"] - 0.5

        # If we're maintaining spreads and we already have orders in place,
        # make sure they're not ours. If they are, we need to adjust, otherwise we'll
        # just work the orders inward until they collide.
        if settings.MAINTAIN_SPREADS:
            if ticker['buy'] == self.exchange.get_highest_buy()['price']:
                self.start_position_buy = ticker["buy"]
            if ticker['sell'] == self.exchange.get_lowest_sell()['price']:
                self.start_position_sell = ticker["sell"]

        # Back off if our spread is too small.
        if self.start_position_buy * (1.00 + settings.MIN_SPREAD) > self.start_position_sell:
            self.start_position_buy *= (1.00 - (settings.MIN_SPREAD / 2))
            self.start_position_sell *= (1.00 + (settings.MIN_SPREAD / 2))

        # Midpoint, used for simpler order placement.
        self.start_position_mid = ticker["mid"]
        logger.info(
            "%s Ticker: Buy: %.*f, Sell: %.*f" %
            (self.instrument['symbol'], tickLog, ticker["buy"], tickLog, ticker["sell"])
        )
        logger.info('Start Positions: Buy: %.*f, Sell: %.*f, Mid: %.*f' %
                    (tickLog, self.start_position_buy, tickLog, self.start_position_sell,
                     tickLog, self.start_position_mid))
        return ticker

    def get_price_offset(self, index):
        """Given an index (1, -1, 2, -2, etc.) return the price for that side of the book.
           Negative is a buy, positive is a sell."""
        # Maintain existing spreads for max profit
        if settings.MAINTAIN_SPREADS:
            start_position = self.start_position_buy if index < 0 else self.start_position_sell
            # First positions (index 1, -1) should start right at start_position, others should branch from there
            index = index + 1 if index < 0 else index - 1
        else:
            # Offset mode: ticker comes from a reference exchange and we define an offset.
            start_position = self.start_position_buy if index < 0 else self.start_position_sell

            # If we're attempting to sell, but our sell price is actually lower than the buy,
            # move over to the sell side.
            if index > 0 and start_position < self.start_position_buy:
                start_position = self.start_position_sell
            # Same for buys.
            if index < 0 and start_position > self.start_position_sell:
                start_position = self.start_position_buy

        #return math.toNearest(start_position * (1 + settings.INTERVAL) ** index, self.instrument['tickSize'])
        return math.toNearest(start_position * (1 + settings.INTERVAL) ** index, 0.5)

    ###
    # 处理订单，创建和取消Orders
    ###
    def place_orders(self):
        """Create order items for use in convergence."""

        buy_orders = []
        sell_orders = []
        # Create orders from the outside in. This is intentional - let's say the inner order gets taken;
        # then we match orders from the outside in, ensuring the fewest number of orders are amended and only
        # a new order is created in the inside. If we did it inside-out, all orders would be amended
        # down and a new order would be created at the outside.
        for i in reversed(range(1, settings.ORDER_PAIRS + 1)):
            if not self.long_position_limit_exceeded():
                buy_orders.append(self.prepare_order(-i))
            if not self.short_position_limit_exceeded():
                sell_orders.append(self.prepare_order(i))

        return self.converge_orders(buy_orders, sell_orders)

    # 准备一个order对象
    def prepare_order(self, index):
        """Create an order object."""

        if settings.RANDOM_ORDER_SIZE is True:
            quantity = random.randint(settings.MIN_ORDER_SIZE, settings.MAX_ORDER_SIZE)
        else:
            quantity = settings.ORDER_START_SIZE + ((abs(index) - 1) * settings.ORDER_STEP_SIZE)

        price = self.get_price_offset(index)  # float类型
        return {  
            'asset':settings.SETTLECURRENCY,
            'symbol': settings.SYMBOL,
            'price': price, 
            'qty': quantity, 
            'side': "1" if index < 0 else "0" , 
            'close_flag':0 ,
            'order_type':1
            }   #1是多 0是卖出空;0 是开仓

    # 统计账户中的活动订单，取消某些订单；
    # 创建新订单
    def converge_orders(self, buy_orders, sell_orders):
        """Converge the orders we currently have in the book with what we want to be in the book.
           This involves amending any open orders and creating new ones if any have filled completely.
           We start from the closest orders outward."""
        max_qty = 1000  # 单个价位允许存在的最大订单

        tickLog = self.exchange.get_instrument()['tickLog']
        
        to_create = []
        to_cancel = []

        #existing_orders_raw = self.exchange.get_orders()
        #existing_orders_raw = self.exchange.gte.open_orders()
        existing_orders_raw =self.exchange.get_orders_http()
        at_price_dict = {}  # 在每个价格上的活动订单数组 price -> [order1,order2...] 

        # 返回的订单没有按照价格合并，所以首先要合并
        print(existing_orders_raw)
        for order in existing_orders_raw:   
            price_str = order['price']
            if not price_str in at_price_dict:
                at_price_dict[price_str] = [order]
            else:
                at_price_dict[price_str].append(order)

        #logger.info(at_price_dict)
        # 先处理buy_orders和sell_orders
        for order in buy_orders:
            price_str = str(order['price'])
            if not price_str in at_price_dict:
                to_create.append(order)
            else:  # 在该价位已经有订单
                arr = at_price_dict[price_str]
                total_qty = sum(int(item['qty'])-int(item['filled_qty']) for item in arr)
                if total_qty + order['qty'] < max_qty:
                    to_create.append(order)

        for order in sell_orders:
            price_str = str(order['price'])
            if not price_str in at_price_dict:
                to_create.append(order)
            else:  # 在该价位已经有订单
                arr = at_price_dict[price_str]
                total_qty = sum(int(item['qty'])-int(item['filled_qty']) for item in arr)
                if total_qty + order['qty'] < max_qty:
                    to_create.append(order)

        # 处理已经存在的订单
        # 超过每个价位的限额，则把改价位所有的订单取消。
        # 超过价格范围的无意义订单也取消，避免占用资金
        #print(at_price_dict)
        for key,arr in at_price_dict.items():
            if float(key) > self.get_price_offset(settings.ORDER_PAIRS + 1) or float(key) < self.get_price_offset( -1 * (settings.ORDER_PAIRS + 1)):
                logger.info('取消价外订单')
                to_cancel.extend(arr)
                continue

            total_qty = sum(int(item['qty'])-int(item['filled_qty']) for item in arr)
            if total_qty > max_qty: #这里要修改具体数值
                #取消所有过量订单
                logger.info('取消过量订单')
                to_cancel.extend(arr)

        if len(to_create) > 0:
            logger.info("Creating %d orders:" % (len(to_create)))
            for order in reversed(to_create):
                logger.info("%4s %d @ %.*f" % (order['side'], order['qty'], tickLog, order['price']))

                self.exchange.gte._curl_gte(path='/v1/api/pc/order/create', query=order, verb='POST')
                #sleep(5)
                
            #self.exchange.create_bulk_orders(to_create)  #暂时没有bulk order 接口

        to_cancel_ids = []
        # Could happen if we exceed a delta limit
        if len(to_cancel) > 0:
            logger.info("Canceling %d orders:" % (len(to_cancel)))
            for order in reversed(to_cancel):
                logger.info("%4s %d @ %.*f" % (order['side'], int(order['qty']), tickLog, float(order['price'])))
            to_cancel_ids = [o['order_id'] for o in to_cancel]
            
            for id in to_cancel_ids:
                self.exchange.gte.cancel(self.settle_currency,self.symbol,id)
                sleep(1)
            #后面记得改成批处理订单
            #self.exchange.gte.cancel_batch(self.settle_currency,self.symbol,to_cancel_ids)

    ###
    # Position Limits
    ###

    def short_position_limit_exceeded(self):
        """Returns True if the short position limit is exceeded"""
        if not settings.CHECK_POSITION_LIMITS:
            return False
        position = self.exchange.get_delta()
        return position <= settings.MIN_POSITION

    def long_position_limit_exceeded(self):
        """Returns True if the long position limit is exceeded"""
        if not settings.CHECK_POSITION_LIMITS:
            return False
        position = self.exchange.get_delta()
        return position >= settings.MAX_POSITION

    ###
    # Sanity
    ##

    def sanity_check(self):
        """Perform checks before placing orders."""

        # Check if OB is empty - if so, can't quote.
        self.exchange.check_if_orderbook_empty()

        # Ensure market is still open.
        #self.exchange.check_market_open()

        # Get ticker, which sets price offsets and prints some debugging info.
        ticker = self.get_ticker()

        # Sanity check:
        if self.get_price_offset(-1) >= ticker["sell"] or self.get_price_offset(1) <= ticker["buy"]:
            logger.error("Buy: %s, Sell: %s" % (self.start_position_buy, self.start_position_sell))
            logger.error("First buy position: %s\nGTE Best Ask: %s\nFirst sell position: %s\nGTE Best Bid: %s" %
                         (self.get_price_offset(-1), ticker["sell"], self.get_price_offset(1), ticker["buy"]))
            logger.error("Sanity check failed, exchange data is inconsistent")
            self.exit()

        # Messaging if the position limits are reached
        if self.long_position_limit_exceeded():
            logger.info("Long delta limit exceeded")
            logger.info("Current Position: %.f, Maximum Position: %.f" %
                        (self.exchange.get_delta(), settings.MAX_POSITION))

        if self.short_position_limit_exceeded():
            logger.info("Short delta limit exceeded")
            logger.info("Current Position: %.f, Minimum Position: %.f" %
                        (self.exchange.get_delta(), settings.MIN_POSITION))

    ###
    # Running
    ###

    def check_file_change(self):
        """Restart if any files we're watching have changed."""
        for f, mtime in watched_files_mtimes:
            if getmtime(f) > mtime:
                self.restart()

    def check_connection(self):
        """Ensure the WS connections are still open."""
        return self.exchange.is_open()

    def exit(self):
        logger.info("Shutting down.")
        try:
            #self.exchange.cancel_all_orders()
            self.exchange.gte.exit()
        except errors.AuthenticationError as e:
            logger.info("Was not authenticated.")
        except Exception as e:
            logger.info("exception: %s" % e)

        sys.exit()

    def run_loop(self):
        while True:
            sys.stdout.write("-----\n")
            #sys.stdout.flush()

            self.check_file_change()
            sleep(settings.LOOP_INTERVAL)

            # This will restart on very short downtime, but if it's longer,
            # the MM will crash entirely as it is unable to connect to the WS on boot.
            if not self.check_connection():
                logger.error("Realtime data connection unexpectedly closed, restarting.")
                self.restart()

            self.sanity_check()  # Ensures health of mm - several cut-out points here
            self.print_status()  # Print skew, delta, etc
            self.place_orders()  # Creates desired orders and converges to existing orders

    def restart(self):
        logger.info("Restarting the market maker...")
        os.execv(sys.executable, [sys.executable] + sys.argv)

#
# Helpers
#


def XBt_to_XBT(XBt):
    return float(XBt) / constants.XBt_TO_XBT


def cost(instrument, quantity, price):
    mult = instrument["multiplier"]
    P = mult * price if mult >= 0 else mult / price
    return abs(quantity * P)


def margin(instrument, quantity, price):
    return cost(instrument, quantity, price) * instrument["initMargin"]


def run():
    logger.info('run:GTE Market Maker Version: %s\n' % constants.VERSION)

    om = OrderManager()
    # Try/except just keeps ctrl-c from printing an ugly stacktrace
    try:
        om.run_loop()
    except (KeyboardInterrupt, SystemExit):
        logger.debug('excption in run_loop()')
        sys.exit()
