from datetime import datetime, timedelta
from typing import Tuple, Union

import functions as function
from api.init import Variables
from bots.variables import Variables as bot
from common.variables import Variables as var
from functions import Function
from ws.init import Variables as ws


class Init(Variables):
    def load_robots(self) -> dict:
        """
        This function loads robot settings from the SQL database from the 'robots'
        table. Since all transactions for the current account are saved in the
        database, the function also checks all robots that have open positions,
        but are not in the 'robots' table. Such robots are also loaded. Their
        status is indicated as 'NOT DEFINED'. The program must see orders and
        trades made from the standard exchange web interface, so reserved robot
        names are provided for each instrument that match the symbol of the
        instrument. Using this names, transactions are taken into account and
        financial results are calculated in the database for each instrument
        separately from the financial results of individual robots. The status of
        such robots is 'RESERVED'.
        """
        db = var.env["MYSQL_DATABASE"]
        # d symbol_list = list(map(lambda x: x[0], self.symbol_list))
        union = ""
        qwr = "select * from ("
        for symbol in self.symbol_list:
            qwr += (
                union
                + "select * from "
                + db
                + ".robots where SYMBOL = '"
                + symbol[0]
                + "' and CATEGORY = '"
                + symbol[1]
                + "' "
            )
            union = "union "
        qwr += ") T where EXCHANGE = '" + self.name + "' order by SORT"
        var.cursor_mysql.execute(qwr)
        for robot in var.cursor_mysql.fetchall():
            emi = robot["EMI"]
            self.robots[emi] = robot
            self.robots[emi]["STATUS"] = "WORK"

        # Searching for unclosed positions by robots that are not in the 'robots' table
        qwr = (
            "select SYMBOL, CATEGORY, EMI, POS from (select EMI, SYMBOL, CATEGORY, \
                sum(CASE WHEN SIDE = 0 THEN QTY WHEN SIDE = 1 THEN \
                    -QTY ELSE 0 END) POS from "
            + db
            + ".coins where EXCHANGE = '"
            + self.name
            + "' and \
                        account = "
            + str(self.user_id)
            + " and SIDE <> -1 group by EMI, \
                            SYMBOL, CATEGORY) res where POS <> 0"
        )
        var.cursor_mysql.execute(qwr)
        defuncts = var.cursor_mysql.fetchall()
        for defunct in defuncts:
            symbol = (defunct["SYMBOL"], defunct["CATEGORY"])
            for emi in self.robots:
                if defunct["EMI"] == emi:
                    break
            else:
                if symbol in self.symbol_list:
                    status = "NOT DEFINED"
                else:
                    status = "NOT IN LIST"
                self.robots[symbol] = {
                    "SYMBOL": defunct["SYMBOL"],
                    "CATEGORY": defunct["CATEGORY"],
                    "EXCHANGE": self.name,
                    "POS": int(defunct["POS"]),
                    "EMI": defunct["EMI"],
                    "STATUS": status,
                    "TIMEFR": None,
                    "CAPITAL": None,
                }

        # Adding RESERVED robots
        union = ""
        qwr = "select * from ("
        for symbol in self.symbol_list:
            qwr += (
                union
                + "select * from (select EMI, SYMBOL, CATEGORY, ACCOUNT, EXCHANGE, \
            sum(CASE WHEN SIDE = 0 THEN QTY WHEN SIDE = 1 THEN \
            -QTY ELSE 0 END) POS from "
                + db
                + ".coins where SIDE <> -1 group by EMI, SYMBOL, CATEGORY, \
            ACCOUNT, EXCHANGE) res where EMI = '"
                + symbol[0]
                + "' and CATEGORY = '"
                + symbol[1]
                + "' "
            )
            union = "union "
        qwr += (
            ") T where EXCHANGE = '"
            + self.name
            + "' and ACCOUNT = "
            + str(self.user_id)
        )
        var.cursor_mysql.execute(qwr)
        reserved = var.cursor_mysql.fetchall()
        for symbol in self.symbol_list:
            for res in reserved:
                if symbol == (res["EMI"], res["CATEGORY"]):
                    pos = int(reserved[0]["POS"])
                    break
            else:
                pos = 0
            if symbol in self.symbol_list or pos != 0:
                self.robots[symbol] = {
                    "SYMBOL": symbol[0],
                    "CATEGORY": symbol[1],  # Не забыть категорию
                    "EXCHANGE": self.name,
                    "POS": pos,
                    "EMI": symbol[0],
                    "STATUS": "RESERVED",
                    "TIMEFR": "None",
                    "CAPITAL": "None",
                }
        for k, v in self.robots.items():
            print(k)
            print(v)
        exit(0)

        # Loading all transactions and calculating financial results for each robot
        for emi in self.robots:
            Function.add_symbol(self, symbol=self.robots[emi]["SYMBOL"])
            var.cursor_mysql.execute(
                "SELECT IFNULL(sum(SUMREAL), 0) SUMREAL, IFNULL(sum(QTY), 0) \
                    POS, IFNULL(sum(abs(QTY)), 0) VOL, IFNULL(sum(COMMISS), 0) \
                        COMMISS, IFNULL(max(TTIME), '1900-01-01 01:01:01') LTIME \
                            FROM (SELECT SUMREAL, (CASE WHEN SIDE = 0 THEN QTY \
                                WHEN SIDE = 1 THEN -QTY ELSE 0 END) QTY, \
                                    COMMISS, TTIME FROM "
                + db
                + ".coins WHERE EMI = %s \
                                        AND ACCOUNT = %s) aa",
                (emi, var.user_id),
            )
            data = var.cursor_mysql.fetchall()
            for row in data:
                for col in row:
                    self.robots[emi][col] = row[col]
                    if col == "POS" or col == "VOL":
                        bot.robots[emi][col] = int(bot.robots[emi][col])
                    if col == "COMMISS" or col == "SUMREAL":
                        bot.robots[emi][col] = float(bot.robots[emi][col])
                    if col == "LTIME":
                        bot.robots[emi][col] = datetime.strptime(
                            str(bot.robots[emi][col]), "%Y-%m-%d %H:%M:%S"
                        )
            bot.robots[emi]["PNL"] = 0
            bot.robots[emi]["lotSize"] = (
                var.instruments[bot.robots[emi]["SYMBOL"]]["lotSize"]
                / var.instruments[bot.robots[emi]["SYMBOL"]]["myMultiplier"]
            )
            if bot.robots[emi]["SYMBOL"] not in bot.full_symbol_list:
                bot.full_symbol_list.append(bot.robots[emi]["SYMBOL"])

        return bot.robots

    def download_data(
        time: datetime, target: datetime, symbol: str, timeframe: str
    ) -> Tuple[Union[list, None], Union[datetime, None]]:
        res = list()
        while target > time:
            data = ws.bitmex.trade_bucketed(
                symbol=symbol, time=time, timeframe=timeframe
            )
            if data:
                last = time
                time = datetime.strptime(
                    str(data[-1]["timestamp"][:19]), "%Y-%m-%dT%H:%M:%S"
                )
                if last == time:
                    return res, time
                res += data
                print(
                    "----> downloaded trade/bucketed, time: "
                    + str(time)
                    + ", rows downloaded:",
                    len(res),
                )
            else:
                message = (
                    "When downloading trade/bucketed data NoneType was recieved "
                    + str(data)
                )
                var.logger.error(message)
                return None, None
        ws.bitmex.logNumFatal = 0

        return res, time

    def load_frames(
        robot: dict,
        frames: dict,
    ) -> Union[dict, None]:
        """
        Loading usual candlestick data from the exchange server. Data is recorded
        in files for each algorithm. Every time you reboot the files are
        overwritten.
        """
        filename = (
            "data/"
            + robot["SYMBOL"]
            + str(robot["TIMEFR"])
            + "_EMI"
            + robot["EMI"]
            + ".txt"
        )
        with open(filename, "w"):
            pass
        target = datetime.utcnow()
        time = target - timedelta(days=bot.missing_days_number)
        delta = timedelta(minutes=robot["TIMEFR"] - target.minute % robot["TIMEFR"])
        target += delta
        target = target.replace(second=0, microsecond=0)

        # Loading timeframe data

        res, time = download_data(
            time=time,
            target=target,
            symbol=robot["SYMBOL"],
            timeframe=var.timefrs[robot["TIMEFR"]],
        )
        if not res:
            return None

        # The 'frames' array is filled with timeframe data.

        for num, row in enumerate(res):
            tm = datetime.strptime(
                row["timestamp"][0:19], "%Y-%m-%dT%H:%M:%S"
            ) - timedelta(minutes=robot["TIMEFR"])
            frames[robot["SYMBOL"]][robot["TIMEFR"]]["data"].append(
                {
                    "date": (tm.year - 2000) * 10000 + tm.month * 100 + tm.day,
                    "time": tm.hour * 10000 + tm.minute * 100,
                    "bid": float(row["open"]),
                    "ask": float(row["open"]),
                    "hi": float(row["high"]),
                    "lo": float(row["low"]),
                    "datetime": tm,
                }
            )
            if num < len(res[:-1]) - 1:
                function.save_timeframes_data(
                    emi=robot["EMI"],
                    symbol=robot["SYMBOL"],
                    timefr=str(robot["TIMEFR"]),
                    frame=frames[robot["SYMBOL"]][robot["TIMEFR"]]["data"][-1],
                )
        frames[robot["SYMBOL"]][robot["TIMEFR"]]["time"] = tm

        message = (
            "Downloaded missing data from the exchange for symbol="
            + robot["SYMBOL"]
            + " TIMEFR="
            + str(robot["TIMEFR"])
        )
        var.logger.info(message)
        function.info_display(message)

        return frames

    def init_timeframes() -> Union[dict, None]:
        for emi in bot.robots:
            # Initialize candlestick timeframe data using 'TIMEFR' fields
            # expressed in minutes.
            if bot.robots[emi]["TIMEFR"] != "None":
                time = datetime.utcnow()
                symbol = bot.robots[emi]["SYMBOL"]
                timefr = bot.robots[emi]["TIMEFR"]
                try:
                    bot.frames[symbol]
                except KeyError:
                    bot.frames[symbol] = dict()
                    try:
                        bot.frames[symbol][timefr]
                    except KeyError:
                        bot.frames[symbol][timefr] = {
                            "time": time,
                            "robots": [],
                            "open": 0,
                            "data": [],
                        }
                        bot.frames[symbol][timefr]["robots"].append(emi)
                        res = load_frames(
                            robot=bot.robots[emi],
                            frames=bot.frames,
                        )
                        if not res:
                            message = (
                                "The emi "
                                + emi
                                + " candle timeframe data was not loaded!"
                            )
                            var.logger.error(message)
                            return None

        return bot.frames

    def delete_unused_robot() -> None:
        """
        Deleting unused robots (if any)
        """
        emi_in_orders = set()
        for val in var.orders.values():
            emi_in_orders.add(val["emi"])
        for emi in bot.robots.copy():
            if bot.robots[emi]["STATUS"] in ("WORK", "OFF"):
                pass
            elif emi in var.symbol_list:
                bot.robots[emi]["STATUS"] = "RESERVED"
            elif bot.robots[emi]["POS"] == 0 and emi not in emi_in_orders:
                function.info_display("Robot EMI=" + emi + ". Deleting from 'robots'")
                del bot.robots[emi]
