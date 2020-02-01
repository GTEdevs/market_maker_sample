
## 简介  
GTE 交易所(www.gte.io) 执行自动做市交易的程序样例。  
It's a sample market making bot for use with [GTE](https://www.gte.io).
由于Gte 的API和bitmex比较像，所以fork了这里(https://github.com/BitMEX/sample-market-maker)的代码，做了对应的修改。例如：  
  -  单个进程只交易一个标的；
  -  支持 GTE 的多交易区、多交易品类（合约、期货）；
  -  支持多、空双向同时持仓；
  -  等等其它特性    

此样例程序提供了：  
  * A `GTEWebsocket` object of GTE websocket connection. All data is realtime and efficiently [fetched via the WebSocket](market_maker/ws/ws_thread.py). This is the fastest way to get market data.
  * A `GTE` object wrapping the REST and WebSocket APIs.
  * Orders may be created, queried, and cancelled via `GTE.place_order()`, `GTE.open_orders_http()` and the like.
  * Withdrawals may be requested (but they still must be confirmed via email and 2FA).
  * Connection errors and WebSocket reconnection is handled for you.
  * [A scaffolding for building your own trading strategies.](#advanced-usage)
  * Out of the box, a simple market making strategy is implemented that blankets the bid and ask.  

## 运行环境

 Python 3.6 + 测试运行通过

## 运行  
  * 在 www.gte.io 注册账户，并生成API Key ；
  * 准备配置文件  
    在项目根目录创建配置文件 settings.py , 参考内容如下：  
    ```json  
    from os.path import join
    import logging

    # Connection/Auth
    # API URL BASE
    API_URL_BASE = "api.gte.io" 
    # websocket URL
    WS_URL = "wss://td.gte.io"

    # The GTE API requires permanent API keys. 
    # Your API Key at gte.io
    API_KEY = "xxxxxxxxxx"
    API_SECRET = "xxxxxxxxxxxxxxxxxxxxxxxxxxxx"

    # Trading Target on GTE. One at a time. If you want to trade multiple instruments, just run multiple instance of this program.
    # settlement currency to trade at
    SETTLECURRENCY = "BTC"
    # Instrument type to trade at
    INSTRUMENTTYPE = "pc"   #永续合约
    # Instrument to market make on GTE.
    SYMBOL = "BTC_USD"  # 暂时只支持一个symbol

    # Order Size & Spread
    # How many pairs of buy/sell orders to keep open
    ORDER_PAIRS = 30

    # ORDER_START_SIZE will be the number of contracts submitted on level 1
    # Number of contracts from level 1 to ORDER_PAIRS - 1 will follow the function
    # [ORDER_START_SIZE + ORDER_STEP_SIZE (Level -1)]
    ORDER_START_SIZE = 100
    ORDER_STEP_SIZE = 173

    # Distance between successive orders, as a percentage (example: 0.005 for 0.5%)
    INTERVAL = 0.005

    # Minimum spread to maintain, in percent, between asks & bids
    MIN_SPREAD = 0.01

    # If True, market-maker will place orders just inside the existing spread and work the interval % outwards,
    # rather than starting in the middle and killing potentially profitable spreads.
    MAINTAIN_SPREADS = True

    # This number defines far much the price of an existing order can be from a desired order before it is amended.
    # This is useful for avoiding unnecessary calls and maintaining your ratelimits.
    #
    # Further information:
    # Each order is designed to be (INTERVAL*n)% away from the spread.
    # If the spread changes and the order has moved outside its bound defined as
    # abs((desired_order['price'] / order['price']) - 1) > settings.RELIST_INTERVAL)
    # it will be resubmitted.
    #
    # 0.01 == 1%
    RELIST_INTERVAL = 0.01


    # Trading Behavior
    # Position limits - set to True to activate. Values are in contracts.
    # If you exceed a position limit, the bot will log and stop quoting that side.
    CHECK_POSITION_LIMITS = False
    MIN_POSITION = -10000
    MAX_POSITION = 10000

    # If True, will only send orders that rest in the book (ExecInst: ParticipateDoNotInitiate).
    # Use to guarantee a maker rebate.
    # However -- orders that would have matched immediately will instead cancel, and you may end up with
    # unexpected delta. Be careful.
    POST_ONLY = False

    # Misc Behavior, Technicals
    # If true, orders not sent to exchange, just say what we would do
    DRY_RUN = False

    # How often to re-check and replace orders.
    # Generally, it's safe to make this short because we're fetching from websockets. But if too many
    # order amend/replaces are done, you may hit a ratelimit. If so, email GTE if you feel you need a higher limit.
    LOOP_INTERVAL = 5

    # Wait times between orders / errors
    API_REST_INTERVAL = 1
    API_ERROR_INTERVAL = 10
    TIMEOUT = 7

    # If we're doing a dry run, use these numbers for BTC balances
    DRY_BTC = 50

    # Available levels: logging.(DEBUG|INFO|WARN|ERROR)
    LOG_LEVEL = logging.INFO

    # To uniquely identify orders placed by this bot, the bot sends a ClOrdID (Client order ID) that is attached
    # to each order so its source can be identified. This keeps the market maker from cancelling orders that are
    # manually placed, or orders placed by another bot.
    #
    # If you are running multiple bots on the same symbol, give them unique ORDERID_PREFIXes - otherwise they will
    # cancel each others' orders.
    # Max length is 13 characters.
    ORDERID_PREFIX = "mm_gte_"

    # If any of these files (and this file) changes, reload the bot.
    WATCHED_FILES = [join('market_maker', 'market_maker.py'), join('market_maker', 'gte.py'), 'settings.py']
    ```
    注意：建议设置`settings.py`里面的`DRY_RUN=True`，用于试运行。 `DRY_RUN=False` 即为向交易所真实下单。  
  * 运行  
    
    在根目录下  
    python marketmaker  

    程序会在当前价格向上/下N到M的档位上挂固定数量A的委托订单，并动态维护账户中的活动订单数量B。N,M,A,B等以及其它策略参数均可以在settings.py中调整。   

## 进阶  

You can implement custom trading strategies using the market maker. `market_maker.OrderManager`
controls placing, updating, and monitoring orders on BitMEX. To implement your own custom
strategy, subclass `market_maker.OrderManager` and override `OrderManager.place_orders()`:

```
from market_maker.market_maker import OrderManager

class CustomOrderManager(OrderManager):
    def place_orders(self) -> None:
        # implement your custom strategy here
```

Your strategy should provide a set of orders. 

Call `self.converge_orders()` to create, amend,
and delete orders on BitMEX as necessary to match what you pass in.  

## 样例输出


```
>> python marketmaker 
2020-02-01 17:38:22,485 - INFO - market_maker - run: - run:GTE Market Maker Version: v0.5
2020-02-01 17:38:22,486 - INFO - ws_thread - connect: - Connecting to wss://td.gte.io
2020-02-01 17:38:22,487 - INFO - ws_thread - __get_auth: - Authenticating with API Key.
2020-02-01 17:38:22,488 - INFO - ws_thread - __connect: - Started thread
2020-02-01 17:38:23,078 - INFO - ws_thread - __on_open: - Websocket Opened.
2020-02-01 17:38:23,490 - INFO - ws_thread - connect: - Connected to WS. Now to subscribe some sample data
2020-02-01 17:38:24,396 - INFO - ws_thread - connect: - Got sample market data. Starting.
2020-02-01 17:38:24,396 - INFO - market_maker - __init__: - Using settle_currency|symbol BTC|BTC_USD.
2020-02-01 17:38:24,397 - INFO - market_maker - __init__: - Order Manager initializing, connecting to GTE. Live run: executing real trades.
2020-02-01 17:38:25,355 - INFO - market_maker - get_ticker: - BTC_USD Ticker: Buy: 8490.0, Sell: 8497.5
2020-02-01 17:38:25,356 - INFO - market_maker - get_ticker: - Start Positions: Buy: 8448.0, Sell: 8539.5, Mid: 8494.0
2020-02-01 17:38:25,357 - INFO - market_maker - print_status: - Current BTC Balance: 0.000005
2020-02-01 17:38:25,357 - INFO - market_maker - print_status: - Current Contract Position: long 72107 ;short 67092
2020-02-01 17:38:25,358 - INFO - market_maker - print_status: - Contracts Traded This Run: long 0; short 0
2020-02-01 17:38:25,709 - INFO - market_maker - converge_orders: - 取消过量订单
2020-02-01 17:38:25,710 - INFO - market_maker - converge_orders: - 取消过量订单
...
2020-02-01 17:38:25,711 - INFO - market_maker - converge_orders: - 取消过量订单
2020-02-01 17:38:25,718 - INFO - market_maker - converge_orders: - 取消价外订单
2020-02-01 17:38:25,719 - INFO - market_maker - converge_orders: - Creating 58 orders:
2020-02-01 17:38:25,719 - INFO - market_maker - converge_orders: -    0 100 @ 8539.5
2020-02-01 17:38:26,905 - INFO - market_maker - converge_orders: -    0 273 @ 8582.0
...
2020-02-01 17:39:25,840 - INFO - market_maker - converge_orders: -    1 4944 @ 7347.0
2020-02-01 17:39:27,046 - INFO - market_maker - converge_orders: -    1 5117 @ 7310.5
2020-02-01 17:39:28,057 - INFO - market_maker - converge_orders: - Canceling 43 orders:
2020-02-01 17:39:28,058 - INFO - market_maker - converge_orders: -    1 5117 @ 7272.0
2020-02-01 17:39:28,059 - INFO - market_maker - converge_orders: -    1 4944 @ 7308.5
...
2020-02-01 17:39:28,065 - INFO - market_maker - converge_orders: -    1 619 @ 8279.0
2020-02-01 17:39:28,065 - INFO - market_maker - converge_orders: -    1 619 @ 8279.0
2020-02-01 17:40:21,750 - INFO - market_maker - get_ticker: - BTC_USD Ticker: Buy: 8490.0, Sell: 8497.5
2020-02-01 17:40:21,751 - INFO - market_maker - get_ticker: - Start Positions: Buy: 8448.0, Sell: 8539.5, Mid: 8494.0
2020-02-01 17:40:21,751 - INFO - market_maker - print_status: - Current BTC Balance: 0.000005
2020-02-01 17:40:21,751 - INFO - market_maker - print_status: - Current Contract Position: long 72107 ;short 0
2020-02-01 17:40:21,752 - INFO - market_maker - print_status: - Contracts Traded This Run: long 0; short -67092
2020-02-01 17:40:22,190 - INFO - market_maker - converge_orders: - 取消过量订单
...
Ctrl^C
2020-02-01 17:40:40,655 - INFO - market_maker - exit: - Shutting down.

```

## 注意事项

By default, the GTE API rate limit is ??? requests per ? minute interval (avg 1/second).

This bot uses the WebSocket and bulk order placement/cancellation to greatly reduce the number of calls sent to the GTE API.

we are able to raise a user's ratelimit without issue.(To do)

## 已知问题

## 联系


