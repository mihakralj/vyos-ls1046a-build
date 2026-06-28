CFLAGS  += -fPIC -Wall -O2 -I$(CURDIR)/include

libfci.a: src/libfci.o
	$(AR) rcs $@ $^

src/libfci.o: src/libfci.c include/libfci.h
	$(CC) $(CFLAGS) -c -o $@ $<

clean:
	rm -f src/*.o libfci.a

.PHONY: clean
